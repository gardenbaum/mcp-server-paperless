"""BulkDocumentEditor on a filter or a saved view: nothing but what was shown, and what became.

The fakes of the "nothing may be written" cases refuse writes: a write fails the test.
"""

from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import (
    FakeDocument,
    FakePaperless,
    FakeSavedView,
    FakeState,
    FakeTag,
    seeded,
)
from harness import bulk_editor
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.bulk import (
    AddTags,
    BulkOutcomes,
    DeleteDocuments,
    ModifyCustomFields,
    ModifyTags,
    RemoveTags,
    ReprocessDocuments,
    SetCorrespondent,
)
from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    AuthenticationFailed,
    ConfirmationRequired,
    Conflict,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    TaskFailed,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.selection import Selection
from mcp_server_paperless.domain.tasks import Unconfirmed
from mcp_server_paperless.domain.views import FilterRule
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.bulk import BulkOperation, DocumentBulkReport, DocumentChange
    from mcp_server_paperless.domain.documents import DocumentPage, DocumentQuery, NameOrId
    from mcp_server_paperless.services.bulk_documents import BulkDocumentEditor

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-69"),
    pytest.mark.decision("decision-71"),
]

DAY = date(2026, 1, 1)
FINANCE, INBOX = 1, 4
INBOX_TAG = AddTags(op="add_tags", tags=["e2e-inbox"])
TRASH = DeleteDocuments(op="delete")
SCANS = {"title_contains": "Scan"}
STILL_RUNNING = " Paperless may still be working on it, and they can change after that."


class World(FakePaperless):
    """A fake whose bulk edit can end in other ways, and that counts its requests."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.sent: list[tuple[int, ...]] = []
        self.stops: list[bool] = []
        self.reads: int = 0
        self.read_error: PaperlessMcpError | None = None
        self.hidden_rows: int = 0
        # What happens instead of the whole change, and how the call ends.
        self.partly: Callable[[], None] | None = None
        self.ends: PaperlessMcpError | Unconfirmed | None = None

    @override
    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        self.reads += 1
        if self.sent and self.read_error is not None:
            raise self.read_error
        found = await super().search_documents(query, page=page, page_size=page_size)
        if self.sent and self.hidden_rows:
            return found.model_copy(update={"hits": found.hits[: -self.hidden_rows]})
        return found

    @override
    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange, *, stop_on_limit: bool = False
    ) -> Unconfirmed | None:
        self.sent.append(tuple(document_ids))
        self.stops.append(stop_on_limit)
        self.reads = 0
        if self.partly is not None:
            self.partly()
        else:
            _ = await super().bulk_edit_documents(document_ids, change)
        if isinstance(self.ends, PaperlessMcpError):
            raise self.ends
        return self.ends


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True, scans: int = 3) -> None:
        self.state: FakeState = seeded()
        self.state.documents += [
            # A search shows the newest first.
            FakeDocument(id=100 + n, title=f"Scan {n}", owner=2, created=DAY - timedelta(days=n))
            for n in range(1, scans + 1)
        ]
        self.fake: World = World(self.state, user=user, writes=writes)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.editor: BulkDocumentEditor = bulk_editor(self.fake, taxonomy)

    def document(self, number: int) -> FakeDocument:
        return next(d for d in self.state.documents if d.id == number)

    def tagged(self, tag: int = INBOX) -> list[int]:
        return [d.id for d in self.state.documents if d.id > 100 and tag in d.tags]

    def view(self, *rules: FilterRule) -> None:
        self.state.saved_views = [
            FakeSavedView(id=9, name="scans", owner=2, filter_rules=rules),
        ]

    async def run(
        self,
        operation: BulkOperation = INBOX_TAG,
        *,
        dry_run: bool = False,
        confirm: bool = False,
        **selection: object,
    ) -> DocumentBulkReport:
        chosen = Selection.model_validate(selection or {"filters": SCANS})
        return await self.editor.edit(chosen, operation, dry_run=dry_run, confirm=confirm)

    async def asked(
        self, operation: BulkOperation = INBOX_TAG, *, confirm: bool = False, **selection: object
    ) -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await self.run(operation, dry_run=False, confirm=confirm, **selection)
        return raised.value


def hint(ids: str) -> str:
    return (
        "Show the user these documents and the effect. Once they agree, call again with the same "
        "filters (or saved_view), exclude_ids and operation, and with "
        f"document_ids=[{ids}] and confirm=true: exactly these are changed, and none if one of "
        "them no longer matches. With filters or saved_view alone, confirm=true is not enough."
    )


# --- nothing but what was shown


@pytest.mark.parametrize("confirm", [False, True])
async def test_a_filter_never_acts_by_itself_and_every_document_is_named(*, confirm: bool) -> None:
    setup = Setup(writes=False)

    refusal = await setup.asked(confirm=confirm)

    assert refusal.message == (
        "Nothing was changed. Confirming would add the tags 'e2e-inbox' to 3 documents "
        "(101 'Scan 1', 102 'Scan 2', 103 'Scan 3')."
    )
    assert refusal.hint == hint("101, 102, 103")


async def test_a_view_never_acts_by_itself_either() -> None:
    setup = Setup(writes=False)
    setup.view(FilterRule(rule_type=0, value="Scan"))

    refusal = await setup.asked(confirm=True, saved_view="scans")

    assert "(101 'Scan 1', 102 'Scan 2', 103 'Scan 3')" in refusal.message


operations = st.sampled_from(
    [
        INBOX_TAG,
        RemoveTags(op="remove_tags", tags=["e2e-inbox"]),
        SetCorrespondent(op="set_correspondent", correspondent="e2e-acme"),
        ModifyCustomFields(op="modify_custom_fields", values={"e2e-string": "x"}),
        TRASH,
        ReprocessDocuments(op="reprocess"),
    ]
)


@given(
    operation=operations,
    dry_run=st.booleans(),
    confirm=st.booleans(),
    by_view=st.booleans(),
    tagged=st.sets(st.integers(101, 103)),
)
async def test_no_call_by_filter_or_view_alone_writes(
    *, operation: BulkOperation, dry_run: bool, confirm: bool, by_view: bool, tagged: set[int]
) -> None:
    setup = Setup(writes=False)
    setup.view(FilterRule(rule_type=0, value="Scan"))
    for number in tagged:
        setup.document(number).tags = [INBOX]
    selection: dict[str, object] = {"saved_view": 9} if by_view else {"filters": SCANS}

    try:
        report = await setup.run(operation, dry_run=dry_run, confirm=confirm, **selection)
    except ConfirmationRequired:
        return
    assert not report.applied


async def test_an_accepted_dialog_changes_exactly_what_it_named() -> None:
    setup = Setup()
    refusal = await setup.asked()

    with accepting(refusal.fingerprint):
        report = await setup.run()

    assert setup.fake.sent == [(101, 102, 103)]
    assert (report.applied, report.matched, report.selected_ids) == (True, 3, (101, 102, 103))
    assert (report.remaining, report.also_matching, report.outcomes) == (0, (), None)


def joins(setup: Setup) -> None:
    setup.state.documents.append(FakeDocument(id=110, title="Scan 10"))


def leaves(setup: Setup) -> None:
    setup.document(102).title = "Another"


def has_it_now(setup: Setup) -> None:
    setup.document(102).tags = [INBOX]


def another_value(setup: Setup) -> None:
    setup.document(102).tags = [FINANCE]


def another_tag(setup: Setup) -> None:
    setup.state.tags = [replace(t, id=44) if t.id == INBOX else t for t in setup.state.tags]


def edited_view(setup: Setup) -> None:
    setup.view(FilterRule(rule_type=0, value="Scan 1"))


@pytest.mark.parametrize("change", [joins, leaves, has_it_now, another_value, edited_view])
async def test_an_accepted_dialog_does_not_cover_what_changed_while_it_was_open(
    change: Callable[[Setup], None],
) -> None:
    setup = Setup(writes=False)
    setup.view(FilterRule(rule_type=0, value="Scan"))
    refusal = await setup.asked(saved_view="scans")
    change(setup)

    with accepting(refusal.fingerprint):
        again = await setup.asked(saved_view="scans", confirm=True)

    assert again.fingerprint != refusal.fingerprint


async def test_a_tag_renamed_meanwhile_stays_the_tag_that_was_meant() -> None:
    setup = Setup()
    refusal = await setup.asked()
    setup.state.tags = [replace(t, name="else") if t.id == INBOX else t for t in setup.state.tags]

    with accepting(refusal.fingerprint):
        _ = await setup.run()

    assert setup.tagged() == [101, 102, 103]


async def test_ids_with_the_filter_change_exactly_these_and_name_what_matches_besides() -> None:
    setup = Setup(scans=5)
    setup.document(104).tags = [INBOX]

    report = await setup.run(confirm=True, filters=SCANS, document_ids=[102, 101])

    assert setup.fake.sent == [(102, 101)]
    assert (report.matched, report.requested, report.affected_count) == (5, 2, 2)
    # 104 matches too, but is as asked already.
    assert (report.also_matching, report.remaining) == ((103, 105), 2)
    assert report.unchanged_count == 0
    assert report.message == (
        "Applied to 2 documents: add the tags 'e2e-inbox' to the selected documents. 2 other "
        "documents match and were not touched (also_matching)."
    )


async def test_one_other_match_is_counted_in_the_singular() -> None:
    report = await Setup().run(confirm=True, filters=SCANS, document_ids=[102, 101])

    assert report.message.endswith(" 1 other document matches and was not touched (also_matching).")


async def test_ids_with_the_filter_still_need_the_confirmation_and_the_same_call_passes() -> None:
    setup = Setup(writes=False)

    refusal = await setup.asked(filters=SCANS, document_ids=[101])

    assert refusal.hint == CONFIRM_HINT


async def test_nothing_is_changed_when_a_named_document_no_longer_matches() -> None:
    setup = Setup(writes=False)
    leaves(setup)

    with pytest.raises(Validation, match="no longer matches: 102"):
        _ = await setup.run(confirm=True, filters=SCANS, document_ids=[101, 102])


async def test_the_next_call_the_refusal_names_goes_through() -> None:
    setup = Setup()
    refusal = await setup.asked(filters=SCANS, exclude_ids=[102])
    assert "document_ids=[101, 103]" in (refusal.hint or "")

    _ = await setup.run(confirm=True, filters=SCANS, exclude_ids=[102], document_ids=[101, 103])

    assert setup.tagged() == [101, 103]


async def test_ids_alone_are_bound_to_the_current_values_too() -> None:
    setup = Setup(writes=False)
    refusal = await setup.asked(document_ids=[101, 102])
    another_value(setup)

    with accepting(refusal.fingerprint):
        again = await setup.asked(document_ids=[101, 102])

    assert again.message == refusal.message
    assert again.fingerprint != refusal.fingerprint


async def told(operation: BulkOperation, setup: Setup | None = None) -> str:
    """The sentence of a confirmation up to its documents."""
    refusal = await (setup or Setup(writes=False)).asked(operation, document_ids=[101])
    return refusal.message.partition("would ")[2].partition(" 1 document ")[0]


async def test_the_new_values_are_in_the_text() -> None:
    values: dict[str, JsonValue] = {
        "e2e-string": "it's\nnew",
        "e2e-integer": 5,
        "e2e-boolean": False,
        "e2e-monetary": "EUR12.5",
        "e2e-select": "beta",
        "e2e-documentlink": [102, 103],
    }
    operation = ModifyCustomFields(op="modify_custom_fields", values=values, remove=["e2e-date"])

    assert await told(operation) == (
        "set the custom fields 'e2e-string' = \"it's new\", 'e2e-integer' = 5, "
        "'e2e-boolean' = false, 'e2e-monetary' = 'EUR 12.50', 'e2e-select' = 'beta', "
        "'e2e-documentlink' = 2 documents (102 'Scan 2', 103 'Scan 3') and remove 'e2e-date' on"
    )


async def test_an_emptied_link_is_told_and_a_long_text_is_cut() -> None:
    emptied = ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": []})
    long = ModifyCustomFields(op="modify_custom_fields", values={"e2e-string": "x" * 100})

    assert await told(emptied) == "set the custom fields 'e2e-documentlink' = no document on"
    assert await told(long) == f"set the custom fields 'e2e-string' = '{'x' * 59}…' on"


async def test_a_changed_value_behind_the_cut_asks_again() -> None:
    setup = Setup(writes=False)

    def long(end: str) -> ModifyCustomFields:
        return ModifyCustomFields(op="modify_custom_fields", values={"e2e-string": "x" * 100 + end})

    first, second = await setup.asked(long("a")), await setup.asked(long("b"))

    assert first.message == second.message
    assert first.fingerprint != second.fingerprint


async def test_every_tag_and_field_is_named_and_the_parents_that_come_along() -> None:
    setup = Setup(writes=False)
    setup.state.tags += [FakeTag(id=50 + n, name=f"t{n}") for n in range(6)]
    setup.state.tags += [FakeTag(id=60, name="deep", parent=2)]
    many: list[NameOrId] = [f"t{n}" for n in range(6)]
    names = ", ".join(f"'t{n}'" for n in range(6))

    assert await told(AddTags(op="add_tags", tags=many), setup) == f"add the tags {names} to"
    assert await told(AddTags(op="add_tags", tags=["deep"]), setup) == (
        "add the tags 'deep' (with their parent tags 'e2e-invoices', 'e2e-finance') to"
    )
    assert await told(ModifyTags(op="modify_tags", add=["deep"], remove=many), setup) == (
        "add the tags 'deep' (with their parent tags 'e2e-invoices', 'e2e-finance') and remove "
        f"the tags {names} on"
    )


async def test_a_parent_the_user_cannot_see_comes_along_by_its_number() -> None:
    setup = Setup(READER[0], writes=False)
    setup.document(101).owner = setup.fake.user.id
    setup.state.tags += [FakeTag(id=70, name="kid", parent=3)]

    assert await told(AddTags(op="add_tags", tags=["kid"]), setup) == (
        "add the tags 'kid' (with their parent tags #3 (a tag you cannot see)) to"
    )


async def test_of_the_fields_to_remove_every_one_is_named() -> None:
    setup = Setup(writes=False)
    fields: list[NameOrId] = [f.name for f in setup.state.custom_fields]
    assert len(fields) > 5
    setup.document(101).custom_fields = {f.id: None for f in setup.state.custom_fields}

    assert await told(ModifyCustomFields(op="modify_custom_fields", remove=fields), setup) == (
        f"remove the custom fields {', '.join(f"'{name}'" for name in fields)} from"
    )


async def test_another_field_of_the_document_may_change_meanwhile() -> None:
    setup = Setup()
    operation = ModifyCustomFields(op="modify_custom_fields", values={"e2e-string": "x"})
    refusal = await setup.asked(operation)
    number = next(f.id for f in setup.state.custom_fields if f.name == "e2e-integer")
    setup.document(102).custom_fields = {number: 5}

    with accepting(refusal.fingerprint):
        report = await setup.run(operation)

    assert report.applied


async def test_a_filter_with_a_document_the_user_may_not_change_names_it_to_leave_out() -> None:
    setup = Setup(READER[0], writes=False, scans=102)
    for document in setup.state.documents:
        document.owner = setup.fake.user.id
    document = setup.document(102)
    document.owner, document.viewers = 2, {setup.fake.user.id}

    with pytest.raises(PermissionDenied, match=r"1 document \(102 'Scan 2'\)"):
        _ = await setup.run()
    preview = await setup.run(dry_run=True)

    assert [row.id for row in preview.blocked] == [102]
    assert "go on" not in preview.message


async def test_hidden_documents_a_link_is_dropped_from_are_told_by_number() -> None:
    setup = Setup(READER[0], writes=False)
    links = next(f.id for f in setup.state.custom_fields if f.name == "e2e-documentlink")
    setup.document(101).owner = setup.fake.user.id
    setup.document(101).custom_fields = {links: [2]}

    refusal = await setup.asked(
        ModifyCustomFields(op="modify_custom_fields", remove=["e2e-documentlink"]),
        document_ids=[101],
    )

    assert refusal.message.endswith(" also changes 1 document besides (#2).")


async def test_of_mirrored_documents_a_hundred_are_named_and_the_rest_counted() -> None:
    setup = Setup(writes=False, scans=102)

    refusal = await setup.asked(
        ModifyCustomFields(
            op="modify_custom_fields", values={"e2e-documentlink": [*range(102, 203)]}
        ),
        document_ids=[101],
    )

    assert "201 'Scan 101' and 1 more)." in refusal.message
    assert "202 " not in refusal.message


@pytest.mark.parametrize("dry_run", [False, True])
async def test_a_view_that_excludes_what_is_not_there_changes_nothing(*, dry_run: bool) -> None:
    # A deleted tag excludes nothing, so more documents match than were meant.
    setup = Setup(writes=False)
    setup.view(FilterRule(rule_type=0, value="Scan"), FilterRule(rule_type=17, value="77"))

    with pytest.raises(Validation) as raised:
        _ = await setup.run(dry_run=dry_run, confirm=True, saved_view=9)
    with pytest.raises(Validation):
        _ = await setup.run(confirm=True, saved_view=9, document_ids=[101])

    assert raised.value.message == (
        "Nothing was changed: the selection excludes tag 77, which you cannot see. If it was "
        "deleted, it excludes nothing and more matches than was meant."
    )
    assert raised.value.hint == (
        "Use filters or a view without it, or pass the document_ids that read_documents shows "
        "instead of the filters."
    )


async def test_what_the_user_may_not_list_does_not_stop_a_change_but_is_told() -> None:
    setup = Setup(READER[0])
    for number in (101, 102, 103):
        setup.document(number).owner = setup.fake.user.id
    setup.fake.user = replace(
        setup.fake.user,
        permissions=frozenset({"view_document", "change_document", "view_tag", "view_savedview"}),
    )
    setup.view(FilterRule(rule_type=0, value="Scan"), FilterRule(rule_type=35, value="2"))
    setup.state.saved_views[0] = replace(setup.state.saved_views[0], owner=None)

    refusal = await setup.asked(saved_view=9)
    preview = await setup.run(saved_view=9, dry_run=True)
    with accepting(refusal.fingerprint):
        _ = await setup.run(saved_view=9)

    told = (
        "; whether user 2 still exists, which the selection excludes, could not be checked: if not,"
        " more matches than was meant"
    )
    assert refusal.message.endswith(f"103 'Scan 3'){told}.")
    assert told in preview.message
    assert setup.tagged() == [101, 102, 103]


async def test_a_selection_without_a_match_changes_nothing() -> None:
    report = await Setup(writes=False).run(filters={"title_contains": "nowhere"}, confirm=True)

    assert (report.matched, report.requested, report.applied) == (0, 0, False)
    assert report.message == "Nothing to change: no document matches."


async def test_a_view_that_filters_nothing_is_refused() -> None:
    setup = Setup(writes=False)
    setup.view()

    with pytest.raises(Validation, match="filters nothing"):
        _ = await setup.run(saved_view=9, dry_run=True)


async def test_a_dry_run_names_the_documents_and_how_to_go_on() -> None:
    setup = Setup(writes=False)
    setup.document(102).tags = [INBOX]

    report = await setup.run(dry_run=True)

    assert (report.matched, report.requested, report.unchanged_count) == (3, 3, 1)
    assert report.selected_ids == (101, 103)
    assert report.message == (
        "Dry run, nothing was changed. Confirming would add the tags 'e2e-inbox' to 2 documents "
        "(101 'Scan 1', 103 'Scan 3'); 1 document already is as asked. To go ahead, call again "
        "without dry_run and with document_ids set to selected_ids besides the same filters or "
        "saved_view; pass confirm=true only if the user already agreed."
    )


async def test_documents_that_match_besides_the_ids_are_not_touched_by_a_dry_run_either() -> None:
    report = await Setup(writes=False).run(dry_run=True, filters=SCANS, document_ids=[101])

    assert (report.selected_ids, report.also_matching, report.remaining) == ((101,), (102, 103), 2)
    assert "2 other documents match and are not touched (also_matching)." in report.message


async def test_others_whose_change_would_be_refused_count_as_not_touched() -> None:
    # Removing the parent is refused where a child stays: there, not on the named document.
    setup = Setup(scans=2)
    setup.document(101).tags = [FINANCE]
    setup.document(102).tags = [FINANCE, 2]

    report = await setup.run(
        RemoveTags(op="remove_tags", tags=["e2e-finance"]),
        confirm=True,
        filters=SCANS,
        document_ids=[101],
    )

    assert (setup.fake.sent, report.also_matching) == ([(101,)], (102,))


# --- limits


async def test_more_than_a_hundred_are_changed_in_several_calls() -> None:
    setup = Setup(scans=102)
    setup.document(101).tags = [INBOX]
    first = await setup.asked()
    with accepting(first.fingerprint):
        report = await setup.run()

    assert "100 documents" in first.message
    assert (report.matched, report.affected_count, report.remaining) == (102, 100, 1)
    assert (report.unchanged_count, report.also_matching) == (1, ())
    assert report.selected_ids == setup.fake.sent[0]
    assert len(report.selected_ids) == 100
    assert report.message.endswith(
        " 1 more document matches: call again with the same filters or saved_view to go on."
    )

    second = await setup.asked()
    with accepting(second.fingerprint):
        report = await setup.run()

    assert (report.affected_count, report.unchanged_count, report.remaining) == (1, 101, 0)
    assert len(setup.tagged()) == 102


async def test_a_preview_counts_what_is_left_for_the_next_call() -> None:
    report = await Setup(writes=False, scans=102).run(dry_run=True)

    assert (len(report.selected_ids), report.remaining) == (100, 2)
    assert "2 more documents match: call again" in report.message


@pytest.mark.parametrize(("matches", "refused"), [(100, False), (101, True)])
async def test_reprocessing_takes_no_more_matches_than_one_call(
    *, matches: int, refused: bool
) -> None:
    setup = Setup(writes=False, scans=matches)
    operation = ReprocessDocuments(op="reprocess")

    with pytest.raises(Validation) as raised:
        _ = await setup.run(operation, confirm=True)

    assert isinstance(raised.value, ConfirmationRequired) is not refused
    if refused:
        assert raised.value.message == (
            "101 documents match, and reprocessed ones still match: at most 100 may be reprocessed "
            "by filters or saved_view."
        )
        assert raised.value.hint == (
            "Narrow the filters, or add document_ids: read_documents shows them."
        )


async def test_named_documents_are_reprocessed_whatever_else_matches() -> None:
    setup = Setup(scans=101)

    report = await setup.run(
        ReprocessDocuments(op="reprocess"), confirm=True, filters=SCANS, document_ids=[101]
    )

    assert (setup.fake.sent, report.remaining) == ([(101,)], 100)


@pytest.mark.parametrize(("matches", "error"), [(1000, ConfirmationRequired), (1001, Validation)])
async def test_at_most_a_thousand_may_match(matches: int, error: type[Validation]) -> None:
    setup = Setup(writes=False, scans=matches)

    with pytest.raises(error) as raised:
        _ = await setup.run(confirm=True)

    assert (type(raised.value) is error, "1001 documents match" in raised.value.message) == (
        True,
        matches == 1001,
    )


# --- what became of a change that failed or was not seen to end


UNKNOWN = "What became of 101, 102, 103 is unknown."
FAILED = "Paperless could not apply the bulk edit: its task failed."


async def seen(setup: Setup, operation: BulkOperation = INBOX_TAG) -> PaperlessMcpError:
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.run(operation, confirm=True, document_ids=[101, 102, 103])
    return raised.value


async def test_a_task_that_failed_half_way_names_what_was_and_was_not_changed() -> None:
    setup = Setup()
    operation = ModifyCustomFields(
        op="modify_custom_fields", values={"e2e-string": "x", "e2e-integer": 5}
    )
    text, number = (
        next(f.id for f in setup.state.custom_fields if f.name == name)
        for name in ("e2e-string", "e2e-integer")
    )

    def partly() -> None:
        setup.document(101).custom_fields = {text: "x", number: 5}
        setup.document(102).custom_fields = {text: "x"}

    setup.fake.partly = partly
    setup.fake.ends = TaskFailed(FAILED, hint="Read again.")

    error = await seen(setup, operation)

    assert isinstance(error, TaskFailed)
    assert error.message == (
        f"{FAILED} Seen after that: as asked 101; not changed 103; unknown 102."
    )
    assert error.hint == "Read again."


@pytest.mark.parametrize(
    "error",
    [
        UpstreamTimeout("Lost."),
        UpstreamUnavailable("Lost.", status=502),
        PaperlessMcpError("Lost."),
    ],
)
async def test_after_a_lost_answer_the_documents_are_read_again(error: PaperlessMcpError) -> None:
    setup = Setup()
    setup.fake.ends = error

    raised = await seen(setup)

    assert raised.message == (f"Lost. Seen after that: as asked 101, 102, 103.{STILL_RUNNING}")
    assert raised.hint == "Read the documents again before repeating the call."
    assert setup.fake.reads == 1


@pytest.mark.parametrize(
    "error",
    [
        UpstreamUnavailable("Slow down.", status=429, hint="Later."),
        PermissionDenied("No.", hint="Later."),
        Validation("No.", hint="Later."),
        NotFound("No.", hint="Later."),
        Conflict("No.", hint="Later."),
        AuthenticationFailed("No.", hint="Later."),
    ],
)
async def test_after_a_429_or_a_refusal_nothing_more_is_asked(error: PaperlessMcpError) -> None:
    setup = Setup()
    setup.fake.partly = lambda: None
    setup.fake.ends = error
    before = error.message

    raised = await seen(setup)

    assert (raised.message, raised.hint, setup.fake.reads) == (before, "Later.", 0)


async def test_a_change_that_could_not_be_followed_reports_what_was_seen() -> None:
    setup = Setup()
    setup.fake.partly = lambda: setup.document(101).tags.append(INBOX)
    setup.fake.ends = Unconfirmed(task_id="task-7", reason="it was still running after 60 seconds")

    report = await setup.run(confirm=True, document_ids=[101, 102, 103])

    assert report.outcomes == BulkOutcomes(changed=(101,), not_changed=(102, 103), unknown=())
    assert f"Seen after that: as asked 101; not changed 102, 103.{STILL_RUNNING}" in (
        report.message
    )
    assert report.unconfirmed_task_id == "task-7"


async def test_an_unconfirmed_change_of_many_does_not_say_go_on() -> None:
    setup = Setup(scans=150)
    setup.fake.partly = lambda: setup.document(101).tags.append(INBOX)
    setup.fake.ends = Unconfirmed(task_id="task-7", reason="it was still running")
    refusal = await setup.asked()

    with accepting(refusal.fingerprint):
        report = await setup.run()

    assert "call again" not in report.message
    assert report.message.endswith(
        " 50 more documents match: do not go on with them before the outcome is known."
    )
    # 50 that were not sent, and the 99 that were not seen changed.
    assert (report.remaining, report.also_matching) == (149, ())


async def test_an_unconfirmed_change_of_named_ones_does_not_look_done_either() -> None:
    setup = Setup()
    setup.fake.partly = lambda: None
    setup.fake.ends = Unconfirmed(task_id="task-7", reason="it was still running")

    report = await setup.run(confirm=True, filters=SCANS, document_ids=[101, 102])

    assert (report.remaining, report.also_matching) == (3, (103,))
    assert report.message.endswith(
        " 1 more document matches: do not go on with it before the outcome is known."
    )


async def test_a_rate_limit_after_the_request_ends_the_call_without_another_read() -> None:
    setup = Setup(scans=150)
    setup.fake.ends = Unconfirmed(task_id="task-7", reason="limited", rate_limited=True)
    refusal = await setup.asked()

    with accepting(refusal.fingerprint):
        report = await setup.run()

    assert (setup.fake.reads, setup.fake.stops) == (0, [True])
    assert report.outcomes is not None
    assert len(report.outcomes.unknown) == 100
    assert report.remaining == 150


@pytest.mark.parametrize(
    "broken", [UpstreamTimeout("Slow."), UpstreamUnavailable("Wait.", status=429)]
)
async def test_a_read_that_fails_never_hides_the_first_error(broken: PaperlessMcpError) -> None:
    setup = Setup()
    setup.fake.ends = TaskFailed(FAILED)
    setup.fake.read_error = broken

    raised = await seen(setup)

    assert raised.message == f"{FAILED} {UNKNOWN}"
    assert setup.fake.reads == 1


async def test_a_trashed_document_is_no_longer_listed() -> None:
    setup = Setup()

    def partly() -> None:
        setup.state.trash.append(setup.document(102))
        setup.state.documents.remove(setup.document(102))

    setup.fake.partly = partly
    setup.fake.ends = TaskFailed(FAILED)

    raised = await seen(setup, TRASH)

    assert raised.message.endswith("as asked 102; not changed 101, 103.")


@pytest.mark.parametrize("operation", [TRASH, INBOX_TAG], ids=["trash", "tags"])
async def test_a_document_the_answer_left_out_is_unknown(operation: BulkOperation) -> None:
    setup = Setup()
    setup.fake.partly = lambda: None
    setup.fake.ends = TaskFailed(FAILED)
    setup.fake.hidden_rows = 1

    raised = await seen(setup, operation)

    assert raised.message.endswith("not changed 101, 102; unknown 103.")


async def test_a_document_that_vanished_is_unknown_unless_it_was_to_be_trashed() -> None:
    setup = Setup()
    setup.fake.partly = lambda: setup.state.documents.remove(setup.document(102))
    setup.fake.ends = TaskFailed(FAILED)

    raised = await seen(setup)

    assert raised.message.endswith("not changed 101, 103; unknown 102.")


async def test_what_reprocessing_did_is_unknown_and_not_read() -> None:
    setup = Setup()
    setup.fake.ends = TaskFailed(FAILED)

    raised = await seen(setup, ReprocessDocuments(op="reprocess"))

    assert raised.message.endswith(UNKNOWN)
    assert setup.fake.reads == 0


OCR_TWICE = "Reading the documents does not show it; repeating may run OCR twice."


@pytest.mark.parametrize("error", [TaskFailed(FAILED, hint="Read."), UpstreamTimeout("Lost.")])
async def test_a_failed_reprocessing_does_not_send_to_the_documents(
    error: PaperlessMcpError,
) -> None:
    setup = Setup()
    setup.fake.ends = error

    raised = await seen(setup, ReprocessDocuments(op="reprocess"))

    assert raised.hint == OCR_TWICE


async def test_an_unconfirmed_reprocessing_does_not_either() -> None:
    setup = Setup()
    setup.fake.ends = Unconfirmed(task_id="task-7", reason="it was still running")

    report = await setup.run(
        ReprocessDocuments(op="reprocess"), confirm=True, document_ids=[101, 102, 103]
    )

    assert f"Read it with get_task. {OCR_TWICE} {UNKNOWN}" in report.message
    assert "documents again" not in report.message
    assert report.remaining == 3


async def test_a_document_that_got_a_child_tag_meanwhile_is_unknown_and_hides_no_other() -> None:
    setup = Setup()
    for number in (101, 102, 103):
        setup.document(number).tags = [FINANCE]

    def partly() -> None:
        setup.document(101).tags = []
        setup.document(102).tags = [FINANCE, 2]

    setup.fake.partly = partly
    setup.fake.ends = TaskFailed(FAILED)

    raised = await seen(setup, ModifyTags(op="modify_tags", remove=["e2e-finance"]))

    assert raised.message.endswith("as asked 101; not changed 103; unknown 102.")


async def test_after_a_lost_answer_the_same_filter_call_changes_nothing_twice() -> None:
    setup = Setup()
    setup.fake.ends = UpstreamTimeout("Lost.")
    refusal = await setup.asked(TRASH)
    with accepting(refusal.fingerprint), pytest.raises(UpstreamTimeout):
        _ = await setup.run(TRASH)

    with pytest.raises(Validation, match="no longer match"):
        _ = await setup.run(TRASH, confirm=True, filters=SCANS, document_ids=[101, 102, 103])
    with accepting(refusal.fingerprint):
        again = await setup.run(TRASH)

    assert (len(setup.fake.sent), again.applied) == (1, False)
