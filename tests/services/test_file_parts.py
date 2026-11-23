"""Files and ZIP archives in parts: windows, what a later part must prove, what is refused."""

import asyncio
import hashlib
import io
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakeFile, FakePaperless, seeded

from mcp_server_paperless.domain.documents import DocumentVersion
from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamUnreachable,
    Validation,
)
from mcp_server_paperless.services.admin_system import SystemAdmin
from mcp_server_paperless.services.files import FileService
from mcp_server_paperless.services.transfer import (
    MAX_FILE_BYTES,
    check_part,
    read_all,
    time_limit,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mcp_server_paperless.domain.documents import FileMetadata, FileVersion
    from mcp_server_paperless.domain.files import ArchivePartReport, FilePart, FilePartReport

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-45")]

WEB = "https://paperless.example/"
LIMIT = 10
ORIGINAL = bytes(range(37))
ARCHIVE = b"%PDF-archive of the document"
CHANGED = "The file changed since the first part; start again with offset=0."


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Metadata(FakePaperless):
    """A Paperless whose file facts a test changes, and that counts how often they are read."""

    update: dict[str, object] | None = None
    reads: int = 0
    # The read from which on the facts are changed (they are right before it).
    since: int = 1

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        self.reads += 1
        found = await super().get_document_metadata(document_id, version_id=version_id)
        return found.model_copy(update=self.update if self.reads >= self.since else None)


def document(original: bytes = ORIGINAL, archive: bytes | None = ARCHIVE) -> FakeDocument:
    return FakeDocument(id=7, title="Müller €", original=original, archive=archive)


def versioned() -> FakeDocument:
    """Document 7 with a newer version 9."""
    found = document()
    newest = b"the newest version of it"
    found.versions.insert(
        0,
        DocumentVersion(
            id=9,
            added=datetime(2026, 11, 12, tzinfo=UTC),
            version_label=None,
            is_root=False,
            checksum=sha(newest),
        ),
    )
    found.version_files = {
        7: FakeFile(ORIGINAL, ARCHIVE, "", "application/pdf"),
        9: FakeFile(newest, None, "", "text/plain"),
    }
    found.original, found.archive, found.mime_type = newest, None, "text/plain"
    return found


def fake_with(
    found: FakeDocument | None = None, facts: dict[str, object] | None = None
) -> Metadata:
    state = seeded()
    state.documents.append(found or document())
    fake = Metadata(state, user=ADMIN[0])
    fake.update = facts
    return fake


def files(fake: FakePaperless, limit: int = LIMIT) -> FileService:
    return FileService(fake, web_url=WEB, max_bytes=limit)


async def first(
    fake: FakePaperless, kind: FileVersion = "original", limit: int = LIMIT
) -> FilePart[FilePartReport]:
    return await files(fake, limit).part(7, kind, offset=0)


@pytest.mark.parametrize("size", [1, 4, 5, 6, 10, None])
@pytest.mark.parametrize(("kind", "whole"), [("original", ORIGINAL), ("archive", ARCHIVE)])
async def test_the_parts_in_order_are_the_file(
    size: int | None, kind: FileVersion, whole: bytes
) -> None:
    service = files(fake_with())
    part = await service.part(7, kind, offset=0, max_bytes=size)
    collected = part.data

    while part.report.next_offset is not None:
        assert len(part.data) == part.report.length == (size or LIMIT)
        assert part.report.next_offset == len(collected)
        part = await service.part(
            7,
            kind,
            version_id=part.report.version_id,
            offset=part.report.next_offset,
            max_bytes=size,
            checksum=part.report.checksum,
        )
        assert part.report.part_sha256 == sha(part.data)
        collected += part.data

    assert collected == whole
    assert sha(collected) == part.report.checksum
    assert part.report.next is None


async def test_a_part_says_what_it_is_and_what_to_call_next() -> None:
    part = await files(fake_with()).part(7, "original", offset=0, max_bytes=4)

    assert part.data == ORIGINAL[:4]
    assert part.report.model_dump() == {
        "document_id": 7,
        "kind": "original",
        "version_id": 7,
        "filename": "2026-11-11 Müller €.pdf",
        "mime_type": "application/pdf",
        "offset": 0,
        "length": 4,
        "total_bytes": 37,
        "next_offset": 4,
        "part_sha256": sha(ORIGINAL[:4]),
        "checksum": sha(ORIGINAL),
        "web_url": "https://paperless.example/documents/7/details",
        "next": (
            "Call again with document_id=7, kind=original, version_id=7, offset=4, max_bytes=4 "
            f"and checksum={sha(ORIGINAL)}; append the parts in order."
        ),
    }


async def test_a_part_is_never_larger_than_the_download_limit() -> None:
    part = await files(fake_with()).part(7, "original", offset=0, max_bytes=LIMIT + 1)

    assert (part.report.length, part.report.next_offset) == (LIMIT, LIMIT)
    assert f"max_bytes={LIMIT} " in (part.report.next or "")


@pytest.mark.parametrize("size", [36, 37, 38])
async def test_a_part_that_reaches_the_end_is_the_last(size: int) -> None:
    part = await files(fake_with(), 100).part(7, "original", offset=0, max_bytes=size)

    assert part.data == ORIGINAL[:size]
    assert part.report.next_offset == (36 if size == 36 else None)


@pytest.mark.parametrize("offset", [37, 38, 10_000])
async def test_a_part_past_the_end_is_empty_and_reads_nothing(offset: int) -> None:
    fake = fake_with()
    fake.state.break_after = 0  # opening it would fail

    part = await files(fake).part(
        7, "original", version_id=7, offset=offset, checksum=sha(ORIGINAL)
    )

    assert (part.data, part.report.length, part.report.next_offset) == (b"", 0, None)
    assert (part.report.total_bytes, part.report.next) == (37, None)


async def test_a_part_reads_no_further_than_its_window() -> None:
    fake = fake_with()
    fake.state.break_after = 2  # the third chunk (bytes 10 to 14) breaks

    part = await files(fake).part(7, "original", offset=0, max_bytes=10)

    assert part.data == ORIGINAL[:10]


async def test_a_connection_that_breaks_within_the_window_delivers_nothing() -> None:
    fake = fake_with()
    fake.state.break_after = 1

    with pytest.raises(UpstreamUnreachable):
        _ = await files(fake).part(7, "original", offset=0, max_bytes=10)


async def test_without_a_version_the_first_part_is_of_the_newest_and_names_it() -> None:
    part = await first(fake_with(versioned()))

    assert (part.report.version_id, part.data) == (9, b"the newest")


async def test_a_named_version_is_read_whatever_is_newer() -> None:
    part = await files(fake_with(versioned())).part(7, "archive", version_id=7, offset=0)

    assert (part.report.version_id, part.data) == (7, ARCHIVE[:LIMIT])


@pytest.mark.parametrize("version_id", [8, 1])
async def test_a_version_the_document_does_not_list_is_not_found(version_id: int) -> None:
    """A trashed version, whose file Paperless would answer, and another document."""
    fake = fake_with(versioned())
    fake.state.trash.append(FakeDocument(id=8, title="old", original=b"secret", root_id=7))

    with pytest.raises(NotFound, match=f"has no version {version_id}") as raised:
        _ = await files(fake).part(7, "original", version_id=version_id, offset=0)
    assert raised.value.hint == "Its versions are in get_document with include versions."
    with pytest.raises(NotFound, match=f"has no version {version_id}"):
        _ = await files(fake).download(7, "original", version_id=version_id)


async def test_a_whole_file_of_a_named_version_is_attached_and_says_which() -> None:
    fake = fake_with(versioned())

    old = await files(fake, 100).download(7, "original", version_id=7)
    new = await files(fake, 100).download(7, "original")

    assert (old.data, old.report.version_id) == (ORIGINAL, 7)
    assert (new.data, new.report.version_id) == (b"the newest version of it", None)
    assert old.report.mime_type == "application/pdf"


async def test_the_note_over_the_limit_says_how_to_read_the_parts() -> None:
    delivery = await files(fake_with()).download(7, "original", version_id=7)

    assert (delivery.data, delivery.report.version_id) == (None, 7)
    assert "Call again with offset=0 to read it in parts" in (delivery.report.note or "")
    assert "save_dir" not in (delivery.report.note or "")


async def test_a_later_part_needs_the_version_and_the_checksum_of_the_first() -> None:
    service = files(fake_with())

    with pytest.raises(Validation, match="needs the checksum") as raised:
        _ = await service.part(7, "original", version_id=7, offset=1)
    assert raised.value.hint == "Repeat the arguments that next in the report names."
    with pytest.raises(Validation, match="needs the version_id") as raised:
        _ = await service.part(7, "original", offset=1, checksum=sha(ORIGINAL))
    assert raised.value.hint == "Repeat the arguments that next in the report names."


@pytest.mark.parametrize("offset", [0, 5])
async def test_a_file_that_changed_since_the_first_part_is_refused(offset: int) -> None:
    fake = fake_with()
    fake.state.break_after = 0  # nothing is opened

    with pytest.raises(Validation) as raised:
        _ = await files(fake).part(
            7, "original", version_id=7, offset=offset, checksum=sha(b"before")
        )

    assert raised.value.message == CHANGED
    assert raised.value.hint == (
        "Repeat the call with offset=0 and without checksum, and use only the new parts."
    )


async def test_a_file_that_changes_while_its_part_is_read_is_refused() -> None:
    fake = fake_with(None, {"archive_checksum": sha(b"reprocessed")})
    fake.since = 2

    with pytest.raises(Validation) as raised:
        _ = await files(fake).part(7, "archive", offset=0)

    assert (raised.value.message, fake.reads) == (CHANGED, 2)


async def test_a_file_shorter_than_its_metadata_says_is_not_delivered() -> None:
    fake = fake_with(None, {"original_size": 38})

    with pytest.raises(PaperlessMcpError, match="other bytes than it says") as raised:
        _ = await files(fake, 100).part(7, "original", offset=0)

    assert raised.value.hint == "Try again; the file may just have changed."


@pytest.mark.parametrize("facts", [{"archive_size": None}, {"archive_checksum": None}])
async def test_a_file_without_size_or_checksum_cannot_be_read_in_parts(
    facts: dict[str, object],
) -> None:
    with pytest.raises(PaperlessMcpError, match="size and checksum") as raised:
        _ = await files(fake_with(None, facts)).part(7, "archive", offset=0)

    assert raised.value.message == (
        "Paperless does not know the size and checksum of the archive of document 7, so it is not "
        "read in parts."
    )
    assert raised.value.hint == "Open it at https://paperless.example/documents/7/details."


async def test_a_document_without_archive_version_has_no_parts_of_it() -> None:
    with pytest.raises(NotFound, match="no archive version"):
        _ = await files(fake_with(document(archive=None))).part(7, "archive", offset=0)


async def test_a_file_over_two_gibibytes_is_not_read_in_parts() -> None:
    at = fake_with(None, {"original_size": MAX_FILE_BYTES})
    over = fake_with(None, {"original_size": MAX_FILE_BYTES + 1})
    over.state.break_after = 0

    assert (await first(at)).report.total_bytes == MAX_FILE_BYTES
    with pytest.raises(PaperlessMcpError, match="larger than 2 GiB") as raised:
        _ = await first(over)
    assert raised.value.hint == "Open the document in the Paperless web UI."


async def test_the_parts_of_a_hidden_document_are_not_found() -> None:
    fake = fake_with()
    hidden = FakePaperless(fake.state, user=READER[0])
    fake.state.documents[-1].owner = 2

    with pytest.raises(NotFound):
        _ = await first(hidden)


@pytest.mark.parametrize(
    ("offset", "max_bytes", "checksum"), [(None, 5, None), (None, None, "x"), (None, 5, "x")]
)
def test_the_arguments_of_a_part_need_an_offset(
    offset: int | None, max_bytes: int | None, checksum: str | None
) -> None:
    with pytest.raises(Validation) as raised:
        check_part(offset, max_bytes, checksum)

    assert raised.value.message == "max_bytes and checksum are for reading a file in parts."
    assert raised.value.hint == "Give offset, too; the first part starts at offset=0."


@pytest.mark.parametrize(("offset", "max_bytes"), [(-1, None), (0, 0), (0, -1)])
def test_an_impossible_window_is_refused(offset: int, max_bytes: int | None) -> None:
    with pytest.raises(Validation, match="0 or more and max_bytes 1 or more") as raised:
        check_part(offset, max_bytes, None)

    assert raised.value.hint == "The first part starts at offset=0."


def test_the_arguments_of_a_whole_file_and_of_a_first_part_are_fine() -> None:
    check_part(None, None, None)
    check_part(0, 1, None)
    check_part(0, None, "x")
    check_part(1, None, "x")


async def test_a_transfer_that_takes_too_long_ends_with_an_error() -> None:
    with pytest.raises(PaperlessMcpError) as raised:
        async with time_limit(0.01):
            _ = await asyncio.Event().wait()

    assert raised.value.message == "Paperless did not send the file within 0 minutes."
    assert raised.value.hint == "Try again later or ask for less."


async def test_the_time_limit_does_not_hide_another_timeout() -> None:
    error = TimeoutError("mine")

    with pytest.raises(TimeoutError, match="mine"):
        async with time_limit(60):
            raise error


async def test_an_archive_over_the_limit_is_given_up_while_it_is_read() -> None:
    class Huge(bytes):
        @override
        def __len__(self) -> int:
            return MAX_FILE_BYTES

    async def chunks(*given: bytes) -> AsyncGenerator[bytes]:
        for chunk in given:
            yield chunk

    _, size, _ = await read_all(chunks(Huge(b"x")), 0, 1)
    assert size == MAX_FILE_BYTES
    with pytest.raises(PaperlessMcpError, match="larger than 2 GiB"):
        _ = await read_all(chunks(Huge(b"x"), b"y"), 0, 1)


# --- ZIP archives


def system(fake: FakePaperless, limit: int = 100) -> SystemAdmin:
    return SystemAdmin(fake, fake, max_bytes=limit)


async def whole(fake: FakePaperless) -> bytes:
    found = await fake.download_documents(
        [1, 7], content="both", compression="none", follow_formatting=False, max_bytes=10**6
    )
    return found.content


async def part_of(
    fake: FakePaperless, offset: int, checksum: str | None = None, max_bytes: int | None = None
) -> FilePart[ArchivePartReport]:
    return await system(fake).part(
        [1, 7], content="both", offset=offset, max_bytes=max_bytes, checksum=checksum
    )


@pytest.mark.parametrize("size", [3, 99, 100, None])
async def test_the_parts_of_an_archive_in_order_are_the_archive(size: int | None) -> None:
    fake = fake_with()
    part = await part_of(fake, 0, max_bytes=size)
    collected = part.data

    while part.report.next_offset is not None:
        assert part.report.next_offset == len(collected)
        assert part.report.length == len(part.data) == (size or 100)
        assert part.report.part_sha256 == sha(part.data)
        part = await part_of(fake, part.report.next_offset, part.report.checksum, size)
        collected += part.data

    assert collected == await whole(fake)
    assert part.report.checksum == sha(collected)
    assert zipfile.is_zipfile(io.BytesIO(collected))


async def test_a_part_of_an_archive_says_what_it_is_and_what_to_call_next() -> None:
    fake = fake_with()
    archive = await whole(fake)

    part = await system(fake).part(
        [1, 7], content="both", compression="lzma", follow_formatting=True, offset=0, max_bytes=9
    )

    assert part.report.model_dump() | {"total_bytes": 0, "checksum": "", "next": ""} == {
        "document_ids": (1, 7),
        "content": "both",
        "filename": "documents.zip",
        "mime_type": "application/zip",
        "offset": 0,
        "length": 9,
        "total_bytes": 0,
        "next_offset": 9,
        "part_sha256": sha(archive[:9]),
        "checksum": "",
        "next": "",
    }
    assert part.report.next == (
        "Call again with the same document_ids, content, compression and follow_formatting, "
        f"offset=9, max_bytes=9 and checksum={part.report.checksum}; append the parts in order."
    )


async def test_a_part_past_the_end_of_an_archive_is_empty() -> None:
    fake = fake_with()
    archive = await whole(fake)

    part = await part_of(fake, len(archive), sha(archive))

    assert (part.data, part.report.next_offset, part.report.total_bytes) == (
        b"",
        None,
        len(archive),
    )


async def test_an_archive_whose_bytes_are_reordered_between_two_parts_is_refused() -> None:
    """PostgreSQL: an edit of a document reorders those with the same created date (decision-45)."""
    fake = fake_with()
    begun = await part_of(fake, 0)
    fake.state.documents.reverse()

    with pytest.raises(Validation) as raised:
        _ = await part_of(fake, begun.report.length, begun.report.checksum)
    assert raised.value.message == CHANGED
    again = await part_of(fake, 0)

    assert again.report.total_bytes == begun.report.total_bytes
    assert again.report.checksum != begun.report.checksum


async def test_a_later_part_of_an_archive_needs_the_checksum() -> None:
    with pytest.raises(Validation, match="needs the checksum"):
        _ = await part_of(fake_with(), 1)


async def test_a_broken_archive_is_no_part_even_if_the_window_was_complete() -> None:
    """The whole archive is read on every call: only that proves the parts belong together."""
    fake = fake_with()
    fake.state.break_after = 30

    with pytest.raises(UpstreamUnreachable):
        _ = await part_of(fake, 0, max_bytes=1)


async def test_the_documents_of_an_archive_in_parts_are_checked() -> None:
    for bad in ([], [1, 1]):
        with pytest.raises(Validation, match="document ids, each once"):
            _ = await system(fake_with()).part(bad, offset=0)
    hidden = FakePaperless(seeded(), user=READER[0])
    with pytest.raises(PermissionDenied):
        _ = await system(hidden).part([2], offset=0)


async def test_the_note_of_an_archive_over_the_limit_says_how_to_read_the_parts() -> None:
    delivery = await system(fake_with(), 10).download([1, 7])

    assert "Call again with offset=0 to read it in parts" in (delivery.report.note or "")
    assert "save_dir" not in (delivery.report.note or "")
