"""FileService.save and SystemAdmin.save: which file, under which name, with which proof."""

import hashlib
import io
import zipfile
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakeFile, FakePaperless, seeded

from mcp_server_paperless.domain.documents import DocumentVersion
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamUnreachable,
    Validation,
)
from mcp_server_paperless.services.admin_system import SAVE_WAIT_SECONDS, SystemAdmin
from mcp_server_paperless.services.file_sink import FileSink
from mcp_server_paperless.services.files import FileService
from mcp_server_paperless.services.transfer import PART_WAIT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Sequence
    from pathlib import Path

    from mcp_server_paperless.domain.admin import ArchiveContent, Compression
    from mcp_server_paperless.domain.documents import FileMetadata, FileVersion
    from mcp_server_paperless.domain.files import OpenedFile

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-45")]

WEB = "https://paperless.example/"
ORIGINAL = b"%PDF-1.4 the original"
ARCHIVE = b"%PDF-archive of the document"
NEWEST = b"the newest version of it"


class Recording(FakePaperless):
    """The fake, noting what is opened; its file facts and proposed names can be changed."""

    opened: tuple[object, ...] = ()
    facts: dict[str, object] | None = None
    unnamed: bool = False

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        found = await super().get_document_metadata(document_id, version_id=version_id)
        return found.model_copy(update=self.facts)

    @override
    @asynccontextmanager
    async def open_document_file(
        self, document_id: int, version: FileVersion, *, version_id: int | None = None
    ) -> AsyncGenerator[OpenedFile]:
        self.opened = (*self.opened, (document_id, version, version_id))
        async with super().open_document_file(document_id, version, version_id=version_id) as found:
            yield replace(found, filename=None) if self.unnamed else found

    @override
    @asynccontextmanager
    async def open_archive(
        self,
        document_ids: Sequence[int],
        *,
        content: ArchiveContent,
        compression: Compression,
        follow_formatting: bool,
        wait_seconds: float,
    ) -> AsyncGenerator[OpenedFile]:
        self.opened = (
            *self.opened,
            (tuple(document_ids), content, compression, follow_formatting, wait_seconds),
        )
        async with super().open_archive(
            document_ids,
            content=content,
            compression=compression,
            follow_formatting=follow_formatting,
            wait_seconds=wait_seconds,
        ) as found:
            yield found


def fake_with(user: str = ADMIN[0]) -> Recording:
    """Document 7 with the versions 9 (newest, text) and 7 (PDF with an archive version)."""
    state = seeded()
    document = FakeDocument(id=7, title="Müller €", original=ORIGINAL, archive=ARCHIVE)
    newest = DocumentVersion(
        id=9,
        added=document.added,
        version_label=None,
        is_root=False,
        checksum=hashlib.sha256(NEWEST).hexdigest(),
    )
    document.versions.insert(0, newest)
    document.version_files = {
        7: FakeFile(ORIGINAL, ARCHIVE, "", "application/pdf"),
        9: FakeFile(NEWEST, b"%PDF-of the newest", "", "text/plain"),
    }
    state.documents.append(document)
    return Recording(state, user=user)


def files(fake: FakePaperless, root: Path) -> FileService:
    return FileService(fake, web_url=WEB, max_bytes=10, sink=FileSink([str(root)]))


def system(fake: FakePaperless, root: Path) -> SystemAdmin:
    return SystemAdmin(fake, fake, max_bytes=10, sink=FileSink([str(root)]))


@pytest.mark.parametrize(
    ("kind", "version_id", "shown", "content"),
    [
        ("original", 7, 7, ORIGINAL),
        ("archive", 7, 7, ARCHIVE),
        ("original", None, 9, NEWEST),
        ("archive", 9, 9, b"%PDF-of the newest"),
    ],
)
async def test_the_chosen_file_is_saved_whatever_the_attach_limit(
    tmp_path: Path, kind: FileVersion, version_id: int | None, shown: int, content: bytes
) -> None:
    fake = fake_with()

    report = await files(fake, tmp_path).save(
        7, kind, version_id=version_id, directory=str(tmp_path)
    )

    [saved] = list(tmp_path.iterdir())  # noqa: ASYNC240 -- a test looking at its directory
    assert saved.read_bytes() == content
    assert report.model_dump() == {
        "document_id": 7,
        "kind": kind,
        "version_id": shown,
        "directory": str(tmp_path),
        "filename": saved.name,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "replaced": False,
        "web_url": "https://paperless.example/documents/7/details",
    }
    assert saved.suffix == (".txt" if content == NEWEST else ".pdf")
    assert saved.name.startswith("2026-11-11 Müller €.")
    assert fake.opened == ((7, kind, shown),)


async def test_a_given_name_is_used_and_an_existing_file_needs_confirm(tmp_path: Path) -> None:
    service = files(fake_with(), tmp_path)
    _ = await service.save(7, "archive", directory=str(tmp_path), save_as="x.pdf")

    with pytest.raises(ConfirmationRequired, match=r"replace the file 'x.pdf' \(18 bytes"):
        _ = await service.save(7, "archive", version_id=7, directory=str(tmp_path), save_as="x.pdf")
    report = await service.save(
        7, "archive", version_id=7, directory=str(tmp_path), save_as="x.pdf", confirm=True
    )

    assert (report.filename, report.replaced) == ("x.pdf", True)
    assert (tmp_path / "x.pdf").read_bytes() == ARCHIVE


async def test_a_name_of_another_kind_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Validation, match=r"is saved with the ending \.pdf"):
        _ = await files(fake_with(), tmp_path).save(
            7, "archive", directory=str(tmp_path), save_as="CLAUDE.md"
        )

    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory


@pytest.mark.parametrize(
    ("mime_type", "title", "ending"),
    [
        ("application/x-sh", "run.command", "run.command.bin"),
        ("message/rfc822", "Mail", "Mail.eml"),
        ("application/msword", "Letter.app", "Letter.app.doc"),
    ],
)
async def test_the_ending_comes_from_the_type_and_not_from_the_title(
    tmp_path: Path, mime_type: str, title: str, ending: str
) -> None:
    fake = fake_with()
    fake.unnamed, fake.facts = True, {"original_filename": title}
    fake.state.documents[-1].version_files[9] = FakeFile(NEWEST, None, "", mime_type)

    report = await files(fake, tmp_path).save(7, "original", directory=str(tmp_path))

    assert report.filename == ending
    assert report.size_bytes == len(NEWEST)


async def test_an_unusable_directory_asks_paperless_nothing(tmp_path: Path) -> None:
    fake = fake_with()
    (tmp_path / "in").mkdir()

    with pytest.raises(NotFound, match="No usable directory"):
        _ = await files(fake, tmp_path / "in").save(7, "original", directory=str(tmp_path))
    with pytest.raises(NotFound, match="No usable directory"):
        _ = await system(fake, tmp_path / "in").save([7], directory=str(tmp_path))

    assert fake.opened == ()


async def test_a_version_the_document_does_not_list_is_not_saved(tmp_path: Path) -> None:
    fake = fake_with()

    with pytest.raises(NotFound, match="has no version 8"):
        _ = await files(fake, tmp_path).save(7, "original", version_id=8, directory=str(tmp_path))

    assert fake.opened == ()


async def test_bytes_that_do_not_match_the_metadata_are_not_saved(tmp_path: Path) -> None:
    fake = fake_with()
    wrong: tuple[tuple[str, dict[str, object]], ...] = (
        ("original", {"original_size": 1}),
        ("original", {"original_checksum": "0" * 64}),
        ("archive", {"archive_checksum": "0" * 64}),
    )
    for kind, facts in wrong:
        fake.facts = facts
        with pytest.raises(PaperlessMcpError, match="other bytes than it says"):
            _ = await files(fake, tmp_path).save(
                7, "archive" if kind == "archive" else "original", directory=str(tmp_path)
            )

    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory


async def test_a_file_paperless_knows_no_size_or_checksum_of_is_saved(tmp_path: Path) -> None:
    fake = fake_with()
    fake.facts = {"archive_size": None, "archive_checksum": None}

    report = await files(fake, tmp_path).save(7, "archive", directory=str(tmp_path))

    assert report.size_bytes == len(b"%PDF-of the newest")


@pytest.mark.parametrize(
    ("kind", "upload", "name"),
    [
        ("original", "scan.PDF", "scan.PDF"),
        ("archive", "scan.JPG", "scan.pdf"),
        ("original", "x.command", "x.command.pdf"),
        ("original", "", "document-7.pdf"),
    ],
)
async def test_a_file_without_a_proposed_name_is_named_after_the_upload(
    tmp_path: Path, kind: FileVersion, upload: str, name: str
) -> None:
    fake = fake_with()
    fake.unnamed, fake.facts = True, {"original_filename": upload}

    report = await files(fake, tmp_path).save(7, kind, version_id=7, directory=str(tmp_path))

    assert report.filename == name


async def test_a_broken_connection_leaves_nothing(tmp_path: Path) -> None:
    fake = fake_with()
    fake.state.break_after = 2

    with pytest.raises(UpstreamUnreachable):
        _ = await files(fake, tmp_path).save(7, "original", directory=str(tmp_path))

    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory


async def test_a_document_without_archive_version_has_none_to_save(tmp_path: Path) -> None:
    fake = fake_with()
    fake.facts = {"has_archive_version": False}

    with pytest.raises(NotFound, match="no archive version"):
        _ = await files(fake, tmp_path).save(7, "archive", directory=str(tmp_path))


async def test_without_a_sink_nothing_is_saved(tmp_path: Path) -> None:
    fake = fake_with()
    with pytest.raises(Validation, match="Saving files is not enabled"):
        _ = await FileService(fake, web_url=WEB, max_bytes=10).save(
            7, "original", directory=str(tmp_path)
        )
    with pytest.raises(Validation, match="Saving files is not enabled"):
        _ = await SystemAdmin(fake, fake, max_bytes=10).save([7], directory=str(tmp_path))


async def test_the_notes_over_the_limit_name_the_directory_where_saving_is_possible(
    tmp_path: Path,
) -> None:
    fake = fake_with()

    file = await files(fake, tmp_path).download(7, "original")
    archive = await system(fake, tmp_path).download([7])

    for note in (file.report.note, archive.report.note):
        assert "Call again with save_dir to save it, or with offset=0 to read it in parts," in (
            note or ""
        )


# --- ZIP archives


async def test_an_archive_is_saved_with_its_proof(tmp_path: Path) -> None:
    fake = fake_with()

    report = await system(fake, tmp_path).save(
        [1, 7], content="both", compression="lzma", follow_formatting=True, directory=str(tmp_path)
    )

    data = (tmp_path / "documents.zip").read_bytes()
    assert zipfile.is_zipfile(io.BytesIO(data))
    assert report.model_dump() == {
        "document_ids": (1, 7),
        "content": "both",
        "directory": str(tmp_path),
        "filename": "documents.zip",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "replaced": False,
    }
    assert fake.opened == (((1, 7), "both", "lzma", True, SAVE_WAIT_SECONDS),)
    assert (SAVE_WAIT_SECONDS, PART_WAIT_SECONDS) == (600, 120)


async def test_an_archive_in_parts_is_asked_for_as_given_and_with_its_own_wait(
    tmp_path: Path,
) -> None:
    fake = fake_with()

    first = await system(fake, tmp_path).part(
        [1, 7], content="both", compression="lzma", follow_formatting=True, offset=0
    )
    plain = await system(fake, tmp_path).part([7], offset=0, max_bytes=10_000)

    assert fake.opened == (
        ((1, 7), "both", "lzma", True, PART_WAIT_SECONDS),
        ((7,), "archive", "none", False, PART_WAIT_SECONDS),
    )
    assert (first.report.content, plain.report.content) == ("both", "archive")


async def test_an_archive_that_would_replace_a_file_is_not_built_unless_confirmed(
    tmp_path: Path,
) -> None:
    fake = fake_with()
    _ = (tmp_path / "x.zip").write_bytes(b"old")

    with pytest.raises(ConfirmationRequired, match=r"replace the file 'x.zip' \(3 bytes"):
        _ = await system(fake, tmp_path).save([7], directory=str(tmp_path), save_as="x.zip")
    assert fake.opened == ()
    report = await system(fake, tmp_path).save(
        [7], directory=str(tmp_path), save_as="x.zip", confirm=True
    )

    assert report.replaced
    assert fake.opened == (((7,), "archive", "none", False, SAVE_WAIT_SECONDS),)


async def test_an_archive_keeps_its_ending(tmp_path: Path) -> None:
    with pytest.raises(Validation, match=r"is saved with the ending \.zip"):
        _ = await system(fake_with(), tmp_path).save([7], directory=str(tmp_path), save_as="run.sh")


async def test_a_hidden_document_or_a_bad_list_saves_nothing(tmp_path: Path) -> None:
    with pytest.raises(PermissionDenied):
        _ = await system(fake_with(READER[0]), tmp_path).save([2], directory=str(tmp_path))
    with pytest.raises(Validation, match="each once"):
        _ = await system(fake_with(), tmp_path).save([7, 7], directory=str(tmp_path))

    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory


async def test_an_archive_that_is_none_is_not_saved(tmp_path: Path) -> None:
    class Empty(Recording):
        @override
        @asynccontextmanager
        async def open_archive(
            self,
            document_ids: Sequence[int],
            *,
            content: ArchiveContent,
            compression: Compression,
            follow_formatting: bool,
            wait_seconds: float,
        ) -> AsyncGenerator[OpenedFile]:
            async with self.open_document_file(7, "original") as found:
                yield found

    with pytest.raises(PaperlessMcpError, match="complete ZIP archive"):
        _ = await system(Empty(fake_with().state, user=ADMIN[0]), tmp_path).save(
            [7], directory=str(tmp_path)
        )


async def refusal(call: Awaitable[object]) -> ConfirmationRequired:
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await call
    return raised.value


@pytest.mark.decision("decision-44")
@pytest.mark.decision("decision-49")
async def test_an_acceptance_names_the_document_the_kind_the_version_and_its_content(
    tmp_path: Path,
) -> None:
    fake = fake_with()
    _ = (tmp_path / "x.pdf").write_bytes(b"old")
    where, service = str(tmp_path), files(fake, tmp_path)

    def asked(kind: FileVersion, version_id: int | None) -> Awaitable[object]:
        return service.save(7, kind, version_id=version_id, directory=where, save_as="x.pdf")

    first = await refusal(asked("archive", 7))
    others = [await refusal(asked("original", 7)), await refusal(asked("archive", 9))]
    assert first.message.endswith(" with the archive version of document 7 (version 7).")
    assert others[0].message.endswith(" with the original of document 7 (version 7).")
    assert others[1].message.endswith(" with the archive version of document 7 (version 9).")
    fake.facts = {"archive_size": 99}
    others.append(await refusal(asked("archive", 7)))
    assert others[-1].message == first.message
    fake.facts = None
    fake.state.documents[-1].version_files[7] = FakeFile(
        ORIGINAL, b"reprocessed", "", "application/pdf"
    )
    others.append(await refusal(asked("archive", 7)))

    assert others[-1].message == first.message
    assert len({first.fingerprint, *(other.fingerprint for other in others)}) == 5


@pytest.mark.decision("decision-44")
@pytest.mark.decision("decision-49")
async def test_an_acceptance_names_the_documents_and_options_of_an_archive(tmp_path: Path) -> None:
    service, where = system(fake_with(), tmp_path), str(tmp_path)
    _ = (tmp_path / "documents.zip").write_bytes(b"old")

    first = await refusal(service.save([7], directory=where))
    others = [
        await refusal(service.save([1], directory=where)),
        await refusal(service.save([7, 1], directory=where)),
        await refusal(service.save([7], directory=where, content="both")),
        await refusal(service.save([7], directory=where, compression="lzma")),
        await refusal(service.save([7], directory=where, follow_formatting=True)),
    ]

    assert first.message.endswith(" with a ZIP of 1 document (7).")
    assert others[1].message.endswith(" with a ZIP of 2 documents (7, 1).")
    assert {other.message for other in others[2:]} == {first.message}
    assert len({first.fingerprint, *(other.fingerprint for other in others)}) == 6
    many = await refusal(service.save(list(range(1, 13)), directory=where))
    assert many.message.endswith(
        " with a ZIP of 12 documents (1, 2, 3, 4, 5, 6, 7, 8, 9, 10 and 2 more)."
    )
