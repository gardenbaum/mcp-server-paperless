"""DocumentSelector: what a selection means, what it refuses, and when a set has changed."""

from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeSavedView, FakeState, seeded
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.selection import MAX_MATCHES, Selection
from mcp_server_paperless.domain.views import FilterRule
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.selection import SELECT_BYTES, DocumentSelector
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.views import ViewService

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import DocumentPage, DocumentQuery, Ordering
    from mcp_server_paperless.services.selection import Selected

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-69")]

PUBLIC, PRIVATE = 1, 2


@final
class Counting(FakePaperless):
    """Keeps the limits of every selection request; can answer fewer rows than it counts."""

    def __init__(self, state: FakeState, *, user: str = ADMIN[0]) -> None:
        super().__init__(state, user=user)
        self.asked: list[tuple[int, int]] = []
        self.short: int = 0
        self.error: PaperlessMcpError | None = None
        self.ignore_ids: bool = False
        self.repeat: bool = False

    @override
    async def select_documents(
        self, query: DocumentQuery, *, limit: int, max_bytes: int
    ) -> DocumentPage:
        self.asked.append((limit, max_bytes))
        if self.error is not None:
            raise self.error
        if self.ignore_ids:
            query = query.model_copy(update={"ids": ()})
        page = await super().select_documents(query, limit=limit, max_bytes=max_bytes)
        if self.repeat:
            return page.model_copy(update={"hits": page.hits * 2, "count": page.count * 2})
        return page.model_copy(update={"hits": page.hits[: len(page.hits) - self.short]})


@final
class Setup:
    def __init__(self, user: str = ADMIN[0]) -> None:
        self.state: FakeState = seeded()
        self.fake: Counting = Counting(self.state, user=user)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        documents = DocumentService(self.fake, self.fake, taxonomy, web_url="https://p.example")
        views = ViewService(self.fake, self.fake, taxonomy, documents)
        self.selector: DocumentSelector = DocumentSelector(self.fake, documents, views, taxonomy)

    def add(self, number: int, title: str = "scratch", **values: object) -> None:
        self.state.documents.append(replace(FakeDocument(id=number, title=title), **values))

    def view(self, *rules: FilterRule, sort: str | None = None) -> None:
        self.state.saved_views.append(
            FakeSavedView(
                id=9, name="mine", owner=2, filter_rules=rules, sort_field=sort, sort_reverse=True
            )
        )

    async def select(
        self, *, ordering: Ordering | None = None, change: bool = False, **selection: object
    ) -> Selected:
        return await self.selector.select(
            Selection.model_validate(selection), ordering=ordering, change=change
        )

    async def refused(self, *, change: bool = False, **selection: object) -> Validation:
        with pytest.raises(Validation) as raised:
            _ = await self.selector.select(Selection.model_validate(selection), change=change)
        return raised.value


def ids(selected: Selected) -> list[int]:
    return [hit.id for hit in selected.hits]


async def test_ids_are_these_documents_in_the_order_given_and_each_once() -> None:
    setup = Setup()

    found = await setup.select(document_ids=(PRIVATE, PUBLIC, PRIVATE, 99))

    assert ids(found) == [PRIVATE, PUBLIC]
    assert (found.matched, found.missing, found.also_matching) == (2, (99,), ())
    assert setup.fake.asked == [(MAX_MATCHES + 1, SELECT_BYTES)]


@pytest.mark.parametrize("ordering", [None, "title"])
async def test_a_document_that_was_not_asked_for_refuses_the_call(
    ordering: Ordering | None,
) -> None:
    setup = Setup()
    setup.fake.ignore_ids = True

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.select(document_ids=(PUBLIC,), ordering=ordering)

    assert raised.value.message == "Paperless named documents that were not asked for: 2."
    assert raised.value.hint == "Nothing was used of its answer; tell the administrator."


async def test_a_document_paperless_names_twice_is_selected_once() -> None:
    setup = Setup()
    setup.fake.repeat = True

    named = await setup.select(document_ids=(PRIVATE, PUBLIC))
    matching = await setup.select(filters={"title_contains": "e2e-p"})

    assert (ids(named), named.matched) == ([PRIVATE, PUBLIC], 2)
    assert (ids(matching), matching.matched) == ([PRIVATE, PUBLIC], 2)


async def test_ids_can_be_ordered() -> None:
    found = await Setup().select(document_ids=(PUBLIC, PRIVATE), ordering="title")

    assert ids(found) == [PRIVATE, PUBLIC]


async def test_filters_take_names_and_the_default_order() -> None:
    setup = Setup()

    found = await setup.select(filters={"tags_any": ["E2E-Finance", "e2e-private"]})

    assert ids(found) == [PRIVATE, PUBLIC]
    assert (found.matched, found.missing) == (2, ())
    assert ids(await setup.select(filters={"title_contains": "e2e-p"}, ordering="-title")) == [
        PUBLIC,
        PRIVATE,
    ]


async def test_filters_that_contradict_each_other_or_name_nothing_are_refused() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="e2e-financ"):
        _ = await setup.select(filters={"tags_all": ["e2e-financ"]})
    refusal = await setup.refused(
        filters={"created_from": "2026-02-01", "created_to": "2026-01-01"}
    )
    assert "created_from is after created_to" in refusal.message
    assert setup.fake.asked == []


async def test_a_saved_view_selects_what_it_shows_in_its_order() -> None:
    setup = Setup()
    assert ids(await setup.select(saved_view="E2E-view")) == [PUBLIC]

    setup.view(FilterRule(rule_type=6, value="1"), sort="title")
    setup.add(5, "a", tags=[1])

    assert ids(await setup.select(saved_view="mine")) == [PUBLIC, 5]
    assert ids(await setup.select(saved_view=9, ordering="title")) == [5, PUBLIC]


async def test_a_view_with_a_deleted_tag_matches_nothing() -> None:
    setup = Setup()
    setup.view(FilterRule(rule_type=6, value="77"))

    assert ids(await setup.select(saved_view="mine", change=True)) == []


async def test_excluded_documents_are_matched_but_not_selected() -> None:
    found = await Setup().select(filters={"title_contains": "e2e-p"}, exclude_ids=(PRIVATE, 99))

    assert ids(found) == [PUBLIC]
    assert found.matched == 2


async def test_the_way_to_choose_is_exactly_one() -> None:
    setup = Setup()

    refusal = await setup.refused()
    assert refusal.message == "No document is chosen."
    assert refusal.hint == "Pass document_ids, filters or saved_view."
    refusal = await setup.refused(filters={"inbox": True}, saved_view="e2e-view")
    assert refusal.message == "filters and saved_view cannot be combined."
    assert refusal.hint == "Pass one of them; a view can be rewritten as filters."
    refusal = await setup.refused(document_ids=(1,), exclude_ids=(2,))
    assert refusal.message == "exclude_ids needs filters or saved_view."
    assert refusal.hint == "Leave the documents out of document_ids instead."
    refusal = await setup.refused(
        document_ids=(3, PUBLIC, 2), filters={"inbox": True}, exclude_ids=(2, PUBLIC)
    )
    assert refusal.message == "Documents both named and excluded: 1, 2."
    assert refusal.hint == "Take them out of document_ids or of exclude_ids."
    assert setup.fake.asked == []


async def test_a_view_0_is_looked_for_as_one() -> None:
    with pytest.raises(NotFound, match="0"):
        _ = await Setup().select(saved_view=0)


@pytest.mark.parametrize(
    ("rule", "gone"),
    [
        (17, "tag 77"),
        (27, "correspondent 77"),
        (29, "document type 77"),
        (31, "storage path 77"),
        (35, "user 77"),
        (40, "custom field 77"),
    ],
)
async def test_an_excluding_rule_for_something_that_is_gone_is_named(rule: int, gone: str) -> None:
    setup = Setup()
    setup.view(FilterRule(rule_type=rule, value="1,77"), FilterRule(rule_type=0, value="e2e"))

    found = await setup.select(saved_view="mine", change=True)

    assert gone in found.excluded_unknown
    notice = "The view excludes what does not exist or is hidden, so it may match more: "
    assert any(text.startswith(notice) and gone in text for text in found.notices)


async def test_rules_for_what_exists_name_nothing() -> None:
    setup = Setup()
    setup.view(FilterRule(rule_type=17, value="3"), FilterRule(rule_type=27, value="1"))

    found = await setup.select(saved_view="mine")

    assert (found.excluded_unknown, found.notices) == ((), ())


async def test_what_the_user_may_not_list_cannot_be_checked_and_is_named() -> None:
    setup = Setup(READER[0])
    reader = setup.fake.user
    setup.fake.user = replace(reader, permissions=reader.permissions | {"view_savedview"})
    setup.state.saved_views.append(
        FakeSavedView(id=9, name="mine", filter_rules=(FilterRule(rule_type=35, value="2"),))
    )

    assert (await setup.select(saved_view="mine")).excluded_unknown == ("user 2",)


@pytest.mark.parametrize("filters", [{}, {"query": None}])
async def test_filters_without_a_filter_are_refused_also_for_reading(
    filters: dict[str, object],
) -> None:
    refusal = await Setup().refused(filters=filters)

    assert refusal.message == "filters holds no filter: that would be every document."
    assert refusal.hint == "Name at least one filter, as in search_documents."


@pytest.mark.parametrize(
    ("name", "value"),
    [("query", " "), ("title_contains", ""), ("tags_all", []), ("custom_field_query", [])],
)
async def test_an_empty_filter_would_match_more_and_is_refused(name: str, value: object) -> None:
    refusal = await Setup().refused(filters={"inbox": True, name: value})

    assert refusal.message == f"Empty filters match everything: {name}."
    assert refusal.hint == "Give them a value or leave them out."


@pytest.mark.parametrize(
    "rules",
    [
        (),
        (FilterRule(rule_type=22, value=""),),
        (FilterRule(rule_type=20, value=" "),),
        (FilterRule(rule_type=42, value="[]"),),
    ],
)
async def test_a_view_that_filters_nothing_can_be_read_but_not_changed(
    rules: tuple[FilterRule, ...],
) -> None:
    setup = Setup()
    setup.view(*rules, sort="title")

    refusal = await setup.refused(saved_view="mine", change=True)
    assert refusal.message == "The saved view filters nothing: it is every document you may see."
    assert refusal.hint == "Use filters, or a view with a rule."
    assert setup.fake.asked == []


async def test_reading_a_view_without_rules_says_that_it_is_everything() -> None:
    setup = Setup()
    setup.view()

    found = await setup.select(saved_view="mine")

    assert set(ids(found)) == {PUBLIC, PRIVATE}
    assert found.notices == ("The saved view filters nothing: it is every document you may see.",)
    assert (await setup.select(saved_view="e2e-view")).notices == ()


async def test_a_misread_query_is_noticed_for_reading_and_refused_for_a_change() -> None:
    setup = Setup()

    found = await setup.select(filters={"query": "acme and invoice"})
    refusal = await setup.refused(filters={"query": "acme and invoice"}, change=True)

    assert len(found.notices) == 1
    assert refusal.message.startswith("The full-text query may match more than was meant: ")
    assert refusal.message.endswith(found.notices[0])
    assert refusal.hint == "Write the query as the notice says, or use structured filters."


async def test_a_misread_query_of_a_view_counts_the_same() -> None:
    setup = Setup()
    setup.view(FilterRule(rule_type=20, value="acme and invoice"))

    assert len((await setup.select(saved_view="mine")).notices) == 1
    assert "may match more" in (await setup.refused(saved_view="mine", change=True)).message


async def test_at_most_a_thousand_documents_may_match() -> None:
    setup = Setup()
    for number in range(10, 10 + MAX_MATCHES):
        setup.add(number)

    found = await setup.select(filters={"title_contains": "scratch"}, change=True)
    assert len(found.hits) == MAX_MATCHES
    # One day, so the id decides: the newest first, without gaps.
    assert ids(found) == list(range(9 + MAX_MATCHES, 9, -1))

    setup.add(9)
    refusal = await setup.refused(filters={"title_contains": "scratch"}, exclude_ids=(9,))
    assert refusal.message == "1001 documents match; at most 1000 may."
    assert refusal.hint == "Narrow the filters (dates, tags, correspondent) or pass document_ids."


async def test_an_answer_that_holds_fewer_rows_than_it_counts_is_refused() -> None:
    setup = Setup()
    setup.fake.short = 1

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.select(filters={"title_contains": "e2e-p"})

    assert raised.value.message == "Paperless named only 1 of the 2 documents that match."
    assert raised.value.hint == "Try again in a moment, or narrow the filters."


async def test_rows_that_are_too_large_are_refused_with_a_way_on() -> None:
    setup = Setup()
    setup.fake.error = AnswerTooLarge("Paperless sent more than 9 bytes.")

    with pytest.raises(AnswerTooLarge) as raised:
        _ = await setup.select(filters={"title_contains": "e2e-p"})

    assert raised.value.message == "The rows of the matching documents are larger than 64 MiB."
    assert (
        raised.value.hint == "Narrow the filters (dates, tags, correspondent) or pass document_ids."
    )


async def test_a_429_ends_the_call_after_one_request() -> None:
    setup = Setup()
    setup.fake.error = UpstreamUnavailable("slow down", status=429)

    with pytest.raises(UpstreamUnavailable):
        _ = await setup.select(filters={"title_contains": "e2e-p"})
    assert len(setup.fake.asked) == 1


# --- ids and what they were chosen by: a set that changed meanwhile -------------------------------


async def test_ids_with_filters_are_these_documents_while_all_match() -> None:
    setup = Setup()
    setup.add(5, tags=[1])
    setup.add(6, tags=[1])

    found = await setup.select(document_ids=(6, PUBLIC), filters={"tags_all": [1]}, change=True)

    assert ids(found) == [6, PUBLIC]
    assert found.matched == 3
    assert found.also_matching == (5,)


async def test_a_document_that_no_longer_matches_refuses_the_call() -> None:
    setup = Setup()
    setup.add(5, tags=[1])
    _ = await setup.select(document_ids=(5, PUBLIC), filters={"tags_all": [1]})
    setup.state.documents[-1].tags = []  # somebody takes the tag off meanwhile

    refusal = await setup.refused(document_ids=(5, PUBLIC), filters={"tags_all": [1]})

    assert refusal.message == "1 of the named documents no longer matches: 5."
    assert refusal.hint == "Choose again with the filters or the view alone, and show the new set."


async def test_a_document_trashed_or_hidden_meanwhile_no_longer_matches() -> None:
    setup = Setup(READER[0])

    refusal = await setup.refused(document_ids=(PUBLIC, PRIVATE, 99), filters={"inbox": True})
    assert refusal.message == "2 of the named documents no longer match: 2, 99."


async def test_an_edited_view_is_compared_by_what_it_matches_now() -> None:
    setup = Setup()
    setup.view(FilterRule(rule_type=6, value="1"))
    assert ids(await setup.select(document_ids=(PUBLIC,), saved_view="mine")) == [PUBLIC]
    setup.state.saved_views[-1].filter_rules = (FilterRule(rule_type=6, value="3"),)

    refusal = await setup.refused(document_ids=(PUBLIC,), saved_view="mine")

    assert "no longer matches: 1" in refusal.message


async def test_ids_with_filters_can_be_ordered() -> None:
    found = await Setup().select(
        document_ids=(PUBLIC, PRIVATE), filters={"title_contains": "e2e"}, ordering="title"
    )

    assert ids(found) == [PRIVATE, PUBLIC]


# --- a restricted user ----------------------------------------------------------------------------


async def test_a_restricted_user_selects_only_what_it_sees_and_hidden_ids_are_missing() -> None:
    setup = Setup(READER[0])

    found = await setup.select(filters={"title_contains": "e2e-p"})
    named = await setup.select(document_ids=(PUBLIC, PRIVATE))

    assert (ids(found), found.matched) == ([PUBLIC], 1)
    assert (ids(named), named.missing) == ([PUBLIC], (PRIVATE,))


async def test_a_user_who_may_not_list_views_is_told_so() -> None:
    with pytest.raises(PermissionDenied):
        _ = await Setup(READER[0]).select(saved_view="e2e-view")


@given(named=st.sets(st.integers(1, 8)), matching=st.sets(st.integers(1, 8)))
async def test_named_documents_are_all_selected_or_the_call_is_refused(
    named: set[int], matching: set[int]
) -> None:
    setup = Setup()
    setup.state.documents.clear()
    for number in range(1, 9):
        setup.add(number, tags=[1] if number in matching else [])
    ids_given, filters = tuple(sorted(named)), {"tags_all": [1]}

    if named <= matching:
        found = await setup.select(document_ids=ids_given, filters=filters, change=True)
        assert set(ids(found)) == (named or matching)
        assert set(found.also_matching) == (matching - named if named else set())
    else:
        _ = await setup.refused(document_ids=ids_given, filters=filters, change=True)
