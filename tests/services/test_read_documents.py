"""ReadDocuments: chosen fields of many documents, text on request, and what did not fit."""

import json
from dataclasses import replace
from typing import TYPE_CHECKING, cast, final, override

import pytest
from fakes.accounts import ADMIN, PUBLIC_NOTE, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.documents import Note
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    AuthenticationFailed,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
)
from mcp_server_paperless.domain.selection import MAX_ANSWER, MAX_TEXT, Selection
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.read_documents import DEFAULT_FIELDS, ReadDocuments
from mcp_server_paperless.services.selection import SELECT_BYTES, DocumentSelector
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.views import ViewService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.documents import DocumentText, Ordering
    from mcp_server_paperless.domain.selection import DocumentRow, DocumentsRead, ReadField
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-70")]

PUBLIC, PRIVATE = 1, 2
WEB = "https://paperless.example"


@final
class Reading(FakePaperless):
    """Keeps what was read; a read of a text can take time or fail, a row can vanish."""

    def __init__(self, state: FakeState, *, user: str) -> None:
        super().__init__(state, user=user)
        self.rows: list[tuple[list[int], list[str], int]] = []
        self.texts: list[int] = []
        self.noted: list[int] = []
        self.now: float = 0.0
        self.seconds: float = 0.0
        self.errors: dict[int, PaperlessMcpError] = {}
        self.vanished: set[int] = set()
        self.row_error: PaperlessMcpError | None = None
        self.listed: list[TaxonomyKind] = []

    @override
    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        self.listed.append(kind)
        return await super().list_taxonomy(kind)

    @override
    async def read_rows(
        self, document_ids: Sequence[int], fields: Sequence[ReadField], *, max_bytes: int
    ) -> tuple[DocumentRow, ...]:
        self.rows.append((list(document_ids), list(fields), max_bytes))
        if self.row_error is not None:
            raise self.row_error
        rows = await super().read_rows(document_ids, fields, max_bytes=max_bytes)
        return tuple(row for row in rows if row.id not in self.vanished)

    @override
    async def get_document_content(
        self, document_id: int, *, version_id: int | None = None
    ) -> DocumentText:
        self.texts.append(document_id)
        self.now += self.seconds
        if document_id in self.errors:
            raise self.errors[document_id]
        return await super().get_document_content(document_id, version_id=version_id)

    @override
    async def list_document_notes(self, document_id: int) -> tuple[Note, ...]:
        self.noted.append(document_id)
        if document_id in self.vanished:
            message = "gone"
            raise NotFound(message)
        return await super().list_document_notes(document_id)


@final
class Setup:
    def __init__(self, user: str = ADMIN[0]) -> None:
        self.state: FakeState = seeded()
        self.fake: Reading = Reading(self.state, user=user)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        documents = DocumentService(self.fake, self.fake, taxonomy, web_url=WEB)
        views = ViewService(self.fake, self.fake, taxonomy, documents)
        self.service: ReadDocuments = ReadDocuments(
            self.fake,
            DocumentSelector(self.fake, documents, views, taxonomy),
            taxonomy,
            web_url=WEB,
            clock=lambda: self.fake.now,
        )

    def add(self, number: int, **values: object) -> None:
        document = FakeDocument(id=number, title=f"scratch {number}")
        self.state.documents.append(replace(document, **values))

    async def read(
        self,
        fields: tuple[ReadField, ...] | None = None,
        *,
        content_chars: int = 2000,
        ordering: Ordering | None = None,
        page: int = 1,
        page_size: int = 25,
        **selection: object,
    ) -> DocumentsRead:
        return await self.service.read(
            Selection.model_validate(selection),
            fields,
            content_chars=content_chars,
            ordering=ordering,
            page=page,
            page_size=page_size,
        )


def ids(found: DocumentsRead) -> list[int]:
    return [row.id for row in found.documents]


def size(found: DocumentsRead) -> int:
    return len(found.model_dump_json())


async def test_the_default_fields_come_with_names_and_a_link() -> None:
    setup = Setup()

    found = await setup.read(document_ids=(PUBLIC, 99))

    assert [row.model_dump(mode="json") for row in found.documents] == [
        {
            "id": 1,
            "web_url": f"{WEB}/documents/1/details",
            "title": "e2e-public-invoice",
            "created": "2026-11-11",
            "correspondent": {"id": 1, "name": "e2e-acme"},
            "document_type": {"id": 1, "name": "e2e-invoice"},
            "tags": [
                {"id": 1, "name": "e2e-finance"},
                {"id": 2, "name": "e2e-invoices"},
                {"id": 4, "name": "e2e-inbox"},
            ],
        }
    ]
    assert found.model_dump(exclude={"documents"}) == {
        "matched": 1,
        "selected_ids": (1,),
        "page": 1,
        "next_page": None,
        "missing": (99,),
        "also_matching": (),
        "truncated": False,
        "left": (),
        "failed": (),
        "notices": (),
    }
    assert setup.fake.rows == [([1], list(DEFAULT_FIELDS), SELECT_BYTES)]
    assert (setup.fake.texts, setup.fake.noted) == ([], [])


async def test_no_fields_are_ids_and_links_only() -> None:
    setup = Setup()

    found = await setup.read((), filters={"title_contains": "e2e-p"})

    assert [set(row.model_dump()) for row in found.documents] == [{"id", "web_url"}] * 2
    assert setup.fake.rows == [([PRIVATE, PUBLIC], [], SELECT_BYTES)]


async def test_every_field_is_shown_readably() -> None:
    setup = Setup()

    found = await setup.read(
        (
            *("title", "added", "modified", "storage_path", "custom_fields"),
            *("archive_serial_number", "owner", "original_file_name", "mime_type"),
            *("page_count", "notes", "version_count", "user_can_change", "content"),
        ),
        document_ids=(PUBLIC, PRIVATE),
    )

    public, private = found.documents
    assert public.storage_path is not None
    assert public.storage_path.name == "e2e-archive"
    values = {field.name: field.value for field in public.custom_fields or ()}
    assert values["e2e-monetary"] == "EUR 12.50"
    assert values["e2e-select"] == "alpha"
    assert public.archive_serial_number == 2
    assert (public.owner, public.page_count, public.version_count) == (None, 1, 1)
    assert (public.original_file_name, public.mime_type) == (
        "e2e-public-invoice.pdf",
        "application/pdf",
    )
    assert [note.note for note in public.notes or ()] == [PUBLIC_NOTE]
    assert public.user_can_change is True
    assert public.added is not None
    assert public.modified is not None
    assert (public.content, public.content_total) == ("E2E public invoice ACME 2026", 28)
    assert public.content_next is None
    assert private.owner is not None
    assert private.owner.name == ADMIN[0]
    assert (private.storage_path, private.notes) == (None, ())
    # The text is no field of the list.
    assert "content" not in setup.fake.rows[0][1]


async def test_names_are_looked_up_only_for_the_fields_asked_for() -> None:
    setup = Setup("e2e-blind")
    # May view documents, and nothing else.
    blind = next(u for u in setup.state.users if u.username == "e2e-blind")
    setup.fake.user = replace(blind, permissions=frozenset({"view_document"}))

    found = await setup.read(("title", "tags"), document_ids=(PUBLIC,))

    assert [tag.name for tag in found.documents[0].tags or ()] == [None, None, None]
    assert setup.fake.listed == ["tag"]


async def test_pages_go_through_one_selection_and_the_first_names_all_of_it() -> None:
    setup = Setup()
    for number in range(10, 15):
        setup.add(number)
    scratch = {"title_contains": "scratch"}

    first = await setup.read(("title",), page_size=2, filters=scratch, exclude_ids=(12,))
    second = await setup.read(("title",), page=2, page_size=2, filters=scratch, exclude_ids=(12,))

    assert (ids(first), ids(second)) == ([14, 13], [11, 10])
    assert (first.matched, first.selected_ids) == (5, (14, 13, 11, 10))
    assert (second.matched, second.selected_ids) == (5, ())
    assert (first.page, first.next_page) == (1, 2)
    assert (second.page, second.next_page) == (2, None)
    last = await setup.read(("title",), page=2, page_size=3, filters=scratch, exclude_ids=(12,))
    assert ids(last) == [10]
    with pytest.raises(NotFound) as raised:
        _ = await setup.read(page=3, page_size=2, filters=scratch, exclude_ids=(12,))
    assert raised.value.message == "Page 3 is beyond the last page of the selection."
    assert raised.value.hint == "Start with page=1; next_page tells how far it goes."


async def test_nothing_that_matches_is_an_empty_page() -> None:
    setup = Setup()

    found = await setup.read(("notes", "content"), filters={"title_contains": "nothing"})

    assert (found.matched, found.documents, found.next_page) == (0, (), None)
    assert setup.fake.noted == []


async def test_the_order_asked_for_holds_for_the_rows() -> None:
    found = await Setup().read(("title",), filters={"title_contains": "e2e-p"}, ordering="-title")

    assert ids(found) == [PUBLIC, PRIVATE]


async def test_a_document_that_vanishes_between_both_reads_is_missing() -> None:
    setup = Setup()
    setup.fake.vanished = {PRIVATE}

    found = await setup.read(("title", "notes"), document_ids=(PRIVATE, PUBLIC, 99))

    assert ids(found) == [PUBLIC]
    assert found.missing == (99, PRIVATE)
    assert setup.fake.noted == [PRIVATE, PUBLIC]


async def test_notices_and_what_matches_too_are_passed_on() -> None:
    setup = Setup()
    setup.add(5, title="e2e extra")
    found = await setup.read(("title",), document_ids=(PUBLIC,), filters={"query": "2026 and e2e"})

    assert found.also_matching == (PRIVATE,)
    later = await setup.read(
        ("title",),
        document_ids=(PUBLIC, 5),
        filters={"title_contains": "e2e"},
        page=2,
        page_size=1,
    )
    # Like selected_ids: on the first page only.
    assert (ids(later), later.also_matching, later.selected_ids) == ([5], (), ())
    assert len(found.notices) == 1


async def test_rows_too_large_to_read_are_refused_with_a_way_on() -> None:
    setup = Setup()
    setup.fake.row_error = AnswerTooLarge("Paperless sent more than 9 bytes.")

    with pytest.raises(AnswerTooLarge) as raised:
        _ = await setup.read(document_ids=(PUBLIC,))

    assert raised.value.message == "The documents of this page are larger than 64 MiB."
    assert raised.value.hint == "Ask for a smaller page_size, or leave out notes or custom_fields."


# --- text -----------------------------------------------------------------------------------------


async def test_the_text_is_cut_and_says_how_to_go_on() -> None:
    found = await Setup().read(("content",), content_chars=10, document_ids=(PUBLIC,))

    [row] = found.documents
    assert (row.content, row.content_total, row.content_next) == ("E2E public", 28, 10)
    assert (found.left, found.truncated) == ((), False)


async def test_the_text_of_a_call_is_at_most_twenty_thousand_characters() -> None:
    setup = Setup()
    for number in (10, 11, 12, 13):
        setup.add(number, content="x" * 9000)

    found = await setup.read(("content",), content_chars=9000, document_ids=(10, 11, 12, 13))

    assert [len(row.content or "") for row in found.documents] == [9000, 9000, 2000, 0]
    assert sum(len(row.content or "") for row in found.documents) == MAX_TEXT
    assert [row.content_next for row in found.documents] == [None, None, 2000, None]
    assert found.documents[3].content is None
    assert found.left == (13,)
    assert not found.truncated
    assert setup.fake.texts == [10, 11, 12]


async def test_exactly_the_limit_leaves_nothing() -> None:
    setup = Setup()
    for number in (10, 11):
        setup.add(number, content="x" * 10_000)

    found = await setup.read(("content",), content_chars=20_000, document_ids=(10, 11))

    assert found.left == ()
    assert [row.content_next for row in found.documents] == [None, None]


async def test_an_empty_text_is_shown_as_one() -> None:
    setup = Setup()
    setup.add(10, content="")

    found = await setup.read(("content",), document_ids=(10,))

    assert (found.documents[0].content, found.documents[0].content_total) == ("", 0)
    assert found.left == ()


async def test_no_text_is_started_after_forty_seconds() -> None:
    setup = Setup()
    for number in (10, 11, 12):
        setup.add(number, content="x")
    setup.fake.seconds = 20.0

    found = await setup.read(("content",), document_ids=(10, 11, 12))

    assert setup.fake.texts == [10, 11]
    assert found.left == (12,)
    assert found.notices == ("No more text was read after 40 seconds.",)

    setup.fake.now, setup.fake.seconds = 0.0, 19.9
    assert (await setup.read(("content",), document_ids=(10, 11, 12))).left == ()


@pytest.mark.parametrize(
    "error",
    [
        UpstreamUnavailable("Paperless is busy.", status=429),
        AuthenticationFailed("Paperless rejected the token."),
        UpstreamUnreachable("Paperless could not be reached."),
    ],
)
async def test_what_concerns_every_request_ends_the_texts_and_names_the_rest(
    error: PaperlessMcpError,
) -> None:
    setup = Setup()
    for number in (10, 11, 12):
        setup.add(number, content="x")
    setup.fake.errors[11] = error

    found = await setup.read(("content",), document_ids=(10, 11, 12))

    assert setup.fake.texts == [10, 11]
    assert found.documents[0].content == "x"
    assert (found.left, found.failed) == ((11, 12), ())
    assert found.notices == (f"Reading text stopped: {error.message}",)


@pytest.mark.parametrize(
    "error",
    [
        UpstreamUnavailable("Paperless answered 500.", status=500),
        UpstreamTimeout("Paperless did not respond in time."),
        PermissionDenied("The Paperless user may not perform this action."),
        PaperlessMcpError("Paperless sent an unexpected response."),
    ],
)
async def test_a_lasting_error_of_one_text_is_named_and_the_others_go_on(
    error: PaperlessMcpError,
) -> None:
    setup = Setup()
    for number in (10, 11, 12, 13):
        setup.add(number, content="x")
    setup.fake.errors |= {10: error, 12: error}

    found = await setup.read(("content",), document_ids=(10, 11, 12, 13))

    assert setup.fake.texts == [10, 11, 12, 13]
    assert [row.content for row in found.documents] == [None, "x", None, "x"]
    # Not among what to ask for again: that would fail for ever.
    assert (found.left, found.failed) == ((), (10, 12))
    assert found.notices == (f"The text of documents 10 and 12 could not be read: {error.message}",)


async def test_different_errors_are_told_apart() -> None:
    setup = Setup()
    for number in (10, 11):
        setup.add(number, content="x")
    setup.fake.errors |= {10: PaperlessMcpError("One."), 11: PaperlessMcpError("Two.")}

    found = await setup.read(("content",), document_ids=(10, 11))

    assert found.notices == (
        "The text of document 10 could not be read: One.",
        "The text of document 11 could not be read: Two.",
    )


async def test_a_text_that_is_gone_meanwhile_does_not_stop_the_others() -> None:
    setup = Setup()
    for number in (10, 11, 12):
        setup.add(number, content="x")
    setup.fake.errors[11] = NotFound("gone")

    found = await setup.read(("content",), document_ids=(10, 11, 12))

    assert setup.fake.texts == [10, 11, 12]
    assert (found.left, found.notices) == ((11,), ())


# --- the size of the answer -----------------------------------------------------------------------


def note(text: str) -> Note:
    return Note(id=1, note=text, created=None, user=None)


async def test_rows_beyond_the_size_of_the_answer_are_left_out_and_named() -> None:
    setup = Setup()
    for number in range(10, 60):
        setup.add(number, notes=[note("n" * 3000)])

    found = await setup.read(("notes",), filters={"title_contains": "scratch"}, page_size=50)

    assert size(found) <= MAX_ANSWER
    # The next row would not have fitted.
    assert size(found) + 3100 > MAX_ANSWER
    assert found.truncated
    assert (*ids(found), *found.left) == found.selected_ids
    assert len(found.left) > 10


async def test_the_size_holds_whatever_the_rows_add_up_to() -> None:
    setup = Setup()
    for number in range(1001, 1101):
        setup.add(number)

    for length in range(1000, 2000, 25):
        for document in setup.state.documents:
            document.notes = [note("n" * length)]
        found = await setup.read(("notes",), filters={"title_contains": "scratch"}, page_size=100)
        assert size(found) <= MAX_ANSWER
        assert found.truncated


async def test_a_row_too_large_alone_is_shown_without_its_largest_field() -> None:
    setup = Setup()
    setup.add(10, notes=[note("n" * MAX_ANSWER)], tags=[1], custom_fields={1: "short"})

    found = await setup.read(("title", "tags", "notes", "custom_fields"), document_ids=(10, PUBLIC))

    first, second = found.documents
    assert (first.notes, first.too_large) == (None, ("notes",))
    assert (first.title, first.tags is not None) == ("scratch 10", True)
    assert [value.value for value in first.custom_fields or ()] == ["short"]
    assert second.too_large is None
    assert (found.truncated, found.left) == (False, ())
    assert size(found) <= MAX_ANSWER


async def test_a_row_can_lose_every_field_that_grows() -> None:
    setup = Setup()
    half = "\x01" * (MAX_ANSWER // 6)  # six characters each in JSON
    setup.add(10, notes=[note(half)], custom_fields={1: half}, content=half)

    found = await setup.read(
        ("title", "notes", "custom_fields", "content"), content_chars=MAX_TEXT, document_ids=(10,)
    )

    [row] = found.documents
    assert set(row.too_large or ()) >= {"notes", "custom_fields"}
    assert size(found) <= MAX_ANSWER
    assert row.title == "scratch 10"


async def test_a_text_too_large_as_json_is_left_out_with_its_numbers() -> None:
    setup = Setup()
    setup.add(10, content="\x01" * MAX_TEXT)

    found = await setup.read(("content",), content_chars=MAX_TEXT, document_ids=(10,))

    [row] = found.documents
    assert (row.content, row.content_total, row.too_large) == (None, MAX_TEXT, ("content",))
    assert row.content_next == 0


async def test_notes_are_refused_to_a_user_who_may_not_view_notes() -> None:
    setup = Setup(READER[0])

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.read(("title", "notes"), document_ids=(PUBLIC,))

    assert raised.value.message == (
        "The Paperless user may not view notes (it needs the permission to view notes)."
    )
    assert raised.value.hint == "Leave notes out of fields."
    assert setup.fake.rows == []
    assert ids(await setup.read(("title",), document_ids=(PUBLIC, PRIVATE))) == [PUBLIC]


@given(
    sizes=st.lists(st.integers(0, 60_000), min_size=1, max_size=8),
    texts=st.integers(0, 12_000),
)
async def test_every_document_is_shown_or_named_and_the_limits_hold(
    sizes: list[int], texts: int
) -> None:
    setup = Setup()
    setup.state.documents.clear()
    for number, length in enumerate(sizes, start=1):
        setup.add(number, notes=[note("n" * length)], content="c" * texts)

    found = await setup.read(
        ("notes", "content"), content_chars=MAX_TEXT, filters={"title_contains": "scratch"}
    )

    assert size(found) <= MAX_ANSWER
    shown = cast("dict[str, object]", json.loads(found.model_dump_json()))
    assert len(json.dumps(shown, separators=(",", ":"), ensure_ascii=False)) <= MAX_ANSWER
    assert sum(len(row.content or "") for row in found.documents) <= MAX_TEXT
    assert set(ids(found)) | set(found.left) == set(range(1, len(sizes) + 1))
    for row in found.documents:
        assert (row.content is None) == (row.id in found.left)


async def test_a_row_too_large_by_its_tags_is_shown_without_them() -> None:
    setup = Setup()
    setup.add(10, tags=list(range(100, 6000)), notes=[note("n")])
    setup.add(11)

    found = await setup.read(("title", "tags", "notes"), document_ids=(10, 11))

    first, second = found.documents
    assert (first.tags, first.too_large) == (None, ("tags",))
    assert [n.note for n in first.notes or ()] == ["n"]
    assert (second.id, second.too_large) == (11, None)
    assert (found.left, found.truncated) == ((), False)


async def test_endless_notices_do_not_fill_the_answer() -> None:
    query = " ".join(f"a{number}:x" for number in range(20_000))

    found = await Setup().read((), filters={"query": query})

    assert size(found) <= MAX_ANSWER
    assert sum(map(len, found.notices)) <= 5000
    assert found.notices[-1].endswith("…")


async def test_notes_nobody_could_check_are_refused() -> None:
    setup = Setup(READER[0])
    setup.fake.vanished = {PUBLIC}

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.read(("notes",), document_ids=(PUBLIC,))

    assert raised.value.message == (
        "It could not be checked whether the user may view notes: the documents are gone."
    )
    assert raised.value.hint == "Choose the documents again, or leave notes out of fields."
    assert setup.fake.rows == []
