"""Looking into the trash: fields, text, thumbnail, file and parts of an entry (PLMCP-78)."""

import hashlib
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import READER
from fakes.paperless import FakePaperless, FakeState

from mcp_server_paperless.domain.documents import DocumentFile
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.versions import VersionUpload
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.trash_scan import TrashScan
from mcp_server_paperless.services.trashed_documents import (
    DOCUMENT_LIMITS,
    NO_FILE,
    RESTORE_FIRST,
    VERSION_LIMITS,
    TrashedDocuments,
)
from services.test_trash import WEB, Setup

if TYPE_CHECKING:
    from mcp_server_paperless.domain.trash import TrashLookup

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-54")]

LIMIT = 100


def looking(
    setup: Setup, fake: FakePaperless | None = None, *, cap: int = 1000, limit: int = LIMIT
) -> TrashedDocuments:
    reader = fake or setup.fake
    documents = DocumentService(reader, reader, setup.taxonomy, web_url=WEB)
    scan = TrashScan(reader, reader, setup.taxonomy, cap=cap)
    return TrashedDocuments(reader, reader, documents, scan, web_url=WEB, max_bytes=limit)


async def versioned(setup: Setup) -> tuple[int, int]:
    """A document in the trash with a first and a second version."""
    number = setup.document("Report")
    [document] = [d for d in setup.state.documents if d.id == number]
    document.content, document.original = "first text", b"first file"
    upload = VersionUpload(filename="v.txt", mime_type="text/plain", content=b"second text")
    task = await setup.writer.get_task(await setup.writer.upload_document_version(number, upload))
    await setup.writer.delete_document(number)
    return number, task.document_ids[0]


async def test_an_entry_comes_with_names_and_says_what_is_not_shown() -> None:
    setup = Setup()
    one = await setup.trashed("Invoice")
    [entry] = [d for d in setup.state.trash if d.id == one]
    entry.tags, entry.document_type = [1], setup.state.document_types[0].id
    entry.content = "the text"

    found = await looking(setup).entry(one, max_chars=10)

    assert (found.id, found.title, found.version_of) == (one, "Invoice", None)
    assert found.document_type
    assert found.document_type.name == setup.state.document_types[0].name
    assert [t.id for t in found.tags] == [1]
    assert found.created_date == found.created
    assert found.archived_file_name == "Invoice.pdf"
    assert (found.user_can_change, found.is_shared_by_requester) == (True, False)
    assert (found.notes, found.custom_fields, found.duplicate_documents) == ((), (), ())
    assert [v.id for v in found.versions] == [one]
    assert found.deleted_at == entry.deleted_at
    assert found.web_url == f"{WEB}/trash"
    assert found.text is None
    assert found.limits == DOCUMENT_LIMITS
    assert "restore_from_trash" in found.limits


async def test_the_text_comes_in_slices() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    [entry] = [d for d in setup.state.trash if d.id == one]
    entry.content = "0123456789"
    service = looking(setup)

    first = (await service.entry(one, text=True, max_chars=6)).text
    second = (await service.entry(one, text=True, offset=6, max_chars=4)).text
    past = (await service.entry(one, text=True, offset=10, max_chars=4)).text

    assert first
    assert (first.content, first.total_chars, first.next_offset) == ("012345", 10, 6)
    assert second
    assert (second.offset, second.content, second.next_offset) == (6, "6789", None)
    assert past
    assert (past.content, past.next_offset) == ("", None)


async def test_text_and_file_of_a_document_are_those_of_its_first_version() -> None:
    setup = Setup()
    number, second = await versioned(setup)
    service = looking(setup)

    found = await service.entry(number, text=True, max_chars=100)
    file = await service.download(number, "original")

    assert found.text
    assert found.text.content == "first text"
    assert [v.id for v in found.versions] == [number]
    assert file.data == b"first file"
    with pytest.raises(NotFound, match=f"Not in the trash: {second}") as raised:
        _ = await service.entry(second, max_chars=100)
    assert f"use {number}" in (raised.value.hint or "")


async def test_a_deleted_version_shows_its_own_text_and_file() -> None:
    setup = Setup()
    document, version = await setup.version("Report")
    service = looking(setup)

    found = await service.entry(version, text=True, max_chars=100)
    file = await service.download(version, "original")

    assert (found.version_of, found.limits) == (document, VERSION_LIMITS)
    assert found.text
    assert found.text.content == "v"
    assert [v.id for v in found.versions] == [document]
    assert file.data == b"v"


class Pages(FakePaperless):
    """Remembers the pages that were asked for; the first request may be too large."""

    too_large: bool = False

    @override
    def __init__(self, state: FakeState, *, user: str) -> None:
        super().__init__(state, user=user)
        self.asked: list[tuple[int, int]] = []

    @override
    async def look_up_in_trash(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> TrashLookup:
        self.asked.append((page, page_size))
        if self.too_large and len(self.asked) == 1:
            message = "too large"
            raise AnswerTooLarge(message)
        return await super().look_up_in_trash(
            document_id, page=page, page_size=page_size, max_bytes=max_bytes
        )


@pytest.mark.parametrize("too_large", [False, True])
async def test_an_entry_beyond_one_request_is_found_page_by_page(
    too_large: bool,  # noqa: FBT001 -- a parametrised flag, not a switch
) -> None:
    setup = Setup()
    numbers = [await setup.trashed(f"doc {n}") for n in range(102)]
    fake = Pages(setup.state, user="admin")
    fake.too_large = too_large

    found = await looking(setup, fake, cap=1).entry(numbers[0], max_chars=1)

    assert found.id == numbers[0]
    assert fake.asked == [(1, 1), (1, 100), (2, 100)]
    with pytest.raises(NotFound, match="Not in the trash: 4242"):
        _ = await looking(setup, fake, cap=1).entry(4242, max_chars=1)


async def test_a_file_comes_with_its_report() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    service = looking(setup)

    original = await service.download(one, "original")
    archive = await service.download(one, "archive")
    thumbnail = await service.thumbnail(one)

    report = original.report
    assert original.data
    assert (report.document_id, report.kind, report.version_id) == (one, "original", one)
    assert (report.delivered, report.size_bytes, report.note) == (True, len(original.data), None)
    assert report.web_url == f"{WEB}/trash"
    assert archive.data
    assert archive.data != original.data
    assert (thumbnail.report.kind, thumbnail.report.mime_type) == ("thumbnail", "image/webp")
    assert (thumbnail.report.delivered, thumbnail.report.max_bytes) == (True, None)
    assert thumbnail.report.size_bytes == len(thumbnail.data)


async def test_a_missing_archive_is_refused_instead_of_sent_as_the_original() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    [entry] = [d for d in setup.state.trash if d.id == one]
    entry.archive = None

    with pytest.raises(NotFound, match="has no archive version"):
        _ = await looking(setup).download(one, "archive")
    with pytest.raises(NotFound, match="has no archive version"):
        _ = await looking(setup).part(one, "archive", offset=0)


async def test_a_file_over_the_limit_is_described_and_read_in_parts() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    [entry] = [d for d in setup.state.trash if d.id == one]
    entry.original = bytes(range(25))
    service = looking(setup, limit=10)

    whole = await service.download(one, "original")
    parts = [await service.part(one, "original", offset=0)]
    while len(parts) < 4 and (following := parts[-1].report.next_offset) is not None:
        parts.append(
            await service.part(one, "original", offset=following, checksum=parts[0].report.checksum)
        )
    small = await service.part(one, "original", offset=0, max_bytes=4)

    assert whole.data is None
    assert (whole.report.delivered, whole.report.size_bytes) == (False, None)
    assert "restore_from_trash" in (whole.report.note or "")
    assert "offset=0" in (whole.report.note or "")
    assert b"".join(p.data for p in parts) == entry.original
    first = parts[0].report
    assert first.checksum == hashlib.sha256(entry.original).hexdigest()
    assert (first.length, first.total_bytes, first.version_id) == (10, 25, one)
    assert first.next == (
        f"Call again with document_id={one}, file=original, offset=10, max_bytes=10 and "
        f"checksum={first.checksum}; append the parts in order and compare the SHA-256 of the "
        "whole with checksum."
    )
    assert parts[-1].report.next is None
    assert small.data == bytes(range(4))
    large = await service.part(one, "original", offset=0, max_bytes=20)
    assert len(large.data) == 10
    with pytest.raises(Validation, match="The file changed"):
        _ = await service.part(one, "original", offset=10, checksum="other")
    with pytest.raises(Validation, match="needs the checksum"):
        _ = await service.part(one, "original", offset=10)


class NoFile(FakePaperless):
    """Paperless has the row, but not the file."""

    empty: bool = False

    @override
    async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
        if self.empty:
            return DocumentFile(content=b"", mime_type="image/webp", filename=None)
        raise NotFound(NO_FILE)

    @override
    async def get_document_file(self, *args: object, **kwargs: object) -> DocumentFile:
        raise NotFound(NO_FILE)


async def test_a_file_paperless_does_not_serve_says_to_restore_first() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    fake = NoFile(setup.state, user="admin")
    service = looking(setup, fake)

    for call in (
        service.download(one, "original"),
        service.part(one, "original", offset=0),
        service.thumbnail(one),
    ):
        with pytest.raises(NotFound) as raised:
            _ = await call
        assert (raised.value.message, raised.value.hint) == (NO_FILE, RESTORE_FIRST)
    fake.empty = True
    with pytest.raises(PaperlessMcpError, match="empty thumbnail"):
        _ = await service.thumbnail(one)


async def test_a_view_grant_does_not_show_what_the_list_does_not() -> None:
    """Paperless would serve the file to anyone with a view grant."""
    setup = Setup(READER[0])
    shared = await setup.trashed("shared")
    [entry] = [d for d in setup.state.trash if d.id == shared]
    entry.viewers.add(setup.fake.user.id)
    nobody = await setup.trashed("nobody's", owner=None)
    service = looking(setup)

    assert (await setup.fake.get_document_file(shared, "original", max_bytes=100)).content
    for call in (
        service.entry(shared, max_chars=1),
        service.download(shared, "original"),
        service.part(shared, "original", offset=0),
        service.thumbnail(shared),
    ):
        with pytest.raises(NotFound, match=f"Not in the trash: {shared}"):
            _ = await call
    assert (await service.entry(nobody, max_chars=1)).owner is None
    assert (await service.download(nobody, "original")).data


async def test_a_user_who_may_not_view_documents_sees_nothing() -> None:
    setup = Setup("e2e-blind")
    one = await setup.trashed("Doc", owner=None)

    with pytest.raises(PermissionDenied):
        _ = await looking(setup).entry(one, max_chars=1)
    with pytest.raises(PermissionDenied):
        _ = await looking(setup).download(one, "original")
