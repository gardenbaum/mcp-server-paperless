"""get_document with include duplicates: the copies of a document and what tells them apart."""

from datetime import date
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.documents import Note, Ref
from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    NotFound,
    PermissionDenied,
)
from mcp_server_paperless.domain.versions import VersionUpload
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.documents import (
    DUPLICATE_LIMIT,
    NOT_COMPARED,
    TRASH_DETAILS,
    UNREAD_DETAILS,
    DocumentService,
)
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Collection

    from mcp_server_paperless.domain.documents import DocumentRecord, DuplicateList
    from mcp_server_paperless.domain.duplicates import DuplicateRecord
    from mcp_server_paperless.domain.trash import TrashedRecord

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-51")]

WEB = "https://paperless.example"
FILE = b"%PDF-1.4 the same file"
NOTE = Note(id=1, note="n", created=None, user=None)


@final
class Reading(FakePaperless):
    """The fake, with a trash read that fails on demand and documents that go away meanwhile."""

    error: Exception | None = None
    gone: frozenset[int] = frozenset()
    denied: bool = False

    def __init__(self, state: FakeState, user: str) -> None:
        super().__init__(state, user=user)
        self.trash_calls: list[tuple[list[int], int, int]] = []

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        if document_id in self.gone:
            message = "gone"
            raise NotFound(message)
        return await super().get_document(document_id)

    @override
    async def get_duplicates(self, document_id: int) -> tuple[DuplicateRecord, ...]:
        if self.denied:
            message = "denied"
            raise PermissionDenied(message)
        return await super().get_duplicates(document_id)

    @override
    async def find_in_trash(
        self, document_ids: Collection[int], *, entries: int, max_bytes: int
    ) -> dict[int, TrashedRecord]:
        self.trash_calls.append((list(document_ids), entries, max_bytes))
        if self.error is not None:
            raise self.error
        return await super().find_in_trash(document_ids, entries=entries, max_bytes=max_bytes)


@final
class Setup:
    def __init__(self, user: str = ADMIN[0]) -> None:
        self.state: FakeState = seeded()
        self.fake = Reading(self.state, user)
        self.service: DocumentService = DocumentService(
            self.fake,
            self.fake,
            TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=60.0),
            web_url=WEB,
        )

    def document(self, number: int, **given: object) -> FakeDocument:
        values: dict[str, object] = {"original": FILE, "owner": 2, "archive": None} | given
        made = FakeDocument(id=number, title=f"Document {number}", **values)  # pyright: ignore[reportArgumentType] -- keyword passthrough
        self.state.documents.append(made)
        return made

    async def delete(self, number: int) -> None:
        writer = FakePaperless(self.state, user=ADMIN[0], allow_writes=True)
        await writer.delete_document(number)

    async def duplicates(self, number: int) -> DuplicateList:
        detail = await self.service.get(number, ("duplicates",))
        assert detail.duplicates is not None
        return detail.duplicates


async def test_without_the_section_nothing_is_read_and_a_single_document_has_none() -> None:
    setup = Setup()
    _ = setup.document(10)

    assert (await setup.service.get(10)).duplicates is None
    found = await setup.duplicates(10)

    assert (found.total, found.truncated, found.documents) == (0, False, ())
    assert found.not_compared == (NOT_COMPARED,)
    assert setup.fake.trash_calls == []


async def test_a_live_duplicate_comes_with_everything_that_tells_it_apart() -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(
        11,
        created=date(2026, 1, 2),
        owner=3,
        correspondent=1,
        document_type=1,
        storage_path=1,
        tags=[1],
        archive_serial_number=7,
        file_name="other.pdf",
        mime_type="text/plain",
        page_count=3,
        notes=[NOTE],
        custom_fields={1: "x"},
    )

    [found] = (await setup.duplicates(10)).documents

    assert (found.id, found.title, found.in_trash, found.deleted_at) == (
        11,
        "Document 11",
        False,
        None,
    )
    assert (found.match, found.created, found.owner) == (
        "same_file",
        date(2026, 1, 2),
        Ref(id=3, name=READER[0]),
    )
    assert found.correspondent == Ref(id=1, name="e2e-acme")
    assert found.document_type == Ref(id=1, name="e2e-invoice")
    assert found.storage_path == Ref(id=1, name="e2e-archive")
    assert found.tags == (Ref(id=1, name="e2e-finance"),)
    assert (found.archive_serial_number, found.original_file_name) == (7, "other.pdf")
    assert (found.mime_type, found.page_count, found.notes_count) == ("text/plain", 3, 1)
    assert found.custom_fields is not None
    assert [(f.name, f.value) for f in found.custom_fields] == [("e2e-string", "x")]
    assert (found.version_count, found.user_can_change) == (1, True)
    assert found.added == setup.state.documents[-1].added
    assert found.web_url == f"{WEB}/documents/11/details"
    assert found.differs == (
        "title",
        "created",
        "owner",
        "correspondent",
        "document_type",
        "storage_path",
        "tags",
        "archive_serial_number",
        "original_file_name",
        "mime_type",
        "page_count",
        "notes_count",
        "custom_fields",
    )


async def test_only_what_differs_is_named_and_the_order_of_tags_and_fields_is_no_difference() -> (
    None
):
    setup = Setup()
    same: dict[str, object] = {"file_name": "x.pdf", "tags": [1, 2], "custom_fields": {1: "a"}}
    first = setup.document(10, **same)
    other = setup.document(11, **{**same, "tags": [2, 1]})
    other.title = first.title
    first.custom_fields = {1: "a", 2: "https://a"}
    other.custom_fields = {2: "https://a", 1: "a"}
    writer = FakePaperless(setup.state, user=ADMIN[0], allow_writes=True)
    upload = VersionUpload(filename="v.txt", mime_type="text/plain", content=b"v")
    _ = await writer.upload_document_version(11, upload)
    other.page_count = first.page_count

    [found] = (await setup.duplicates(10)).documents

    assert found.version_count == 2
    assert found.differs == ("version_count",)
    assert found.match == "same_file"


async def test_an_archive_match_and_an_unknown_checksum_are_told_from_the_same_file() -> None:
    setup = Setup()
    _ = setup.document(10, archive=b"archive")
    _ = setup.document(11, original=b"archive")
    unknown = setup.document(12)
    unknown.versions = [unknown.versions[0].model_copy(update={"checksum": None})]
    upper = setup.document(13)
    checksum = (upper.versions[0].checksum or "").upper()
    upper.versions = [upper.versions[0].model_copy(update={"checksum": checksum})]

    found = await setup.duplicates(10)

    assert {d.id: d.match for d in found.documents} == {11: "archive", 12: None, 13: "same_file"}


async def test_a_duplicate_in_the_trash_comes_last_with_what_paperless_tells_of_it() -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(11, tags=[1], notes=[NOTE], custom_fields={1: "x"}, page_count=9)
    _ = setup.document(12)
    await setup.delete(11)

    found = await setup.duplicates(10)

    assert [d.id for d in found.documents] == [12, 11]
    trashed = found.documents[1]
    assert (trashed.in_trash, trashed.match, trashed.web_url) == (True, "same_file", None)
    assert trashed.deleted_at == setup.state.trash[0].deleted_at
    assert trashed.tags == (Ref(id=1, name="e2e-finance"),)
    # In the trash with the document, so Paperless does not tell them.
    assert (trashed.notes_count, trashed.custom_fields, trashed.version_count) == (None,) * 3
    assert (trashed.page_count, trashed.user_can_change) == (None, None)
    assert trashed.differs == ("title", "tags", "original_file_name")
    assert found.not_compared == (NOT_COMPARED, TRASH_DETAILS)
    assert setup.fake.trash_calls == [([11], 500, 64 * 1024 * 1024)]


async def test_a_trashed_duplicate_that_is_only_shared_has_its_title_and_date_only() -> None:
    setup = Setup(READER[0])
    _ = setup.document(10, owner=None)
    _ = setup.document(11, viewers={3}, tags=[1])
    _ = setup.document(12)  # hidden
    await setup.delete(11)

    found = await setup.duplicates(10)

    [shared] = found.documents
    assert (shared.id, shared.title, shared.in_trash) == (11, "Document 11", True)
    assert (shared.match, shared.tags, shared.created, shared.differs) == (None, None, None, ())
    assert (found.total, found.truncated) == (1, False)
    assert found.not_compared == (NOT_COMPARED, TRASH_DETAILS, UNREAD_DETAILS.format(ids="11"))
    assert "12" not in str(found)


@pytest.mark.parametrize(
    "error", [AnswerTooLarge("x"), AnswerCompressed("x"), PermissionDenied("x")]
)
async def test_a_trash_that_cannot_be_read_leaves_its_details_out(error: Exception) -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(11)
    _ = setup.document(12)
    await setup.delete(11)
    setup.fake.error = error

    found = await setup.duplicates(10)

    assert [(d.id, d.match) for d in found.documents] == [(12, "same_file"), (11, None)]
    assert found.not_compared[-1] == UNREAD_DETAILS.format(ids="11")


async def test_a_duplicate_that_goes_away_meanwhile_is_still_named() -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(11)
    setup.fake.gone = frozenset({11})

    found = await setup.duplicates(10)

    [gone] = found.documents
    assert (gone.id, gone.title, gone.match) == (11, "Document 11", None)
    assert found.not_compared == (NOT_COMPARED, UNREAD_DETAILS.format(ids="11"))


async def test_many_duplicates_are_cut_and_counted() -> None:
    setup = Setup()
    for number in range(100, 100 + DUPLICATE_LIMIT + 2):
        _ = setup.document(number)

    found = await setup.duplicates(100)

    assert (found.total, found.truncated) == (DUPLICATE_LIMIT + 1, True)
    assert [d.id for d in found.documents] == list(range(101, 101 + DUPLICATE_LIMIT))
    assert found.not_compared[-1] == "Only the first 25 of 26 duplicates are listed."


async def test_exactly_the_limit_is_not_cut() -> None:
    setup = Setup()
    for number in range(100, 100 + DUPLICATE_LIMIT + 1):
        _ = setup.document(number)

    found = await setup.duplicates(100)

    assert (found.total, found.truncated) == (DUPLICATE_LIMIT, False)
    assert found.not_compared == (NOT_COMPARED,)


async def test_a_list_the_user_may_not_read_is_unavailable() -> None:
    setup = Setup()
    setup.fake.denied = True

    detail = await setup.service.get(1, ("duplicates", "versions"))

    assert detail.unavailable == ("duplicates",)
    assert detail.duplicates is None
    assert detail.versions is not None


def test_the_texts_about_what_was_not_compared() -> None:
    assert NOT_COMPARED == (
        "Paperless compares original uploads and archive files, not later versions; documents "
        "this user cannot see are never compared."
    )
    assert TRASH_DETAILS == (
        "In the trash Paperless tells no notes, custom fields, page count or versions."
    )
    assert UNREAD_DETAILS == (
        "Only the title could be read of: {ids} (in the trash and not owned, beyond the first 500 "
        "entries, a trash over 64 MiB, or changed meanwhile)."
    )
