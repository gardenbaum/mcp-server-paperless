"""FileService: thumbnails and downloads with a size limit, decided before the bytes are read."""

from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.documents import FileMetadata
from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
)
from mcp_server_paperless.domain.versions import VersionUpload
from mcp_server_paperless.services.files import TRASHED_HINT, FileService

if TYPE_CHECKING:
    from collections.abc import Collection

    from mcp_server_paperless.domain.documents import DocumentFile, FileVersion

pytestmark = pytest.mark.anyio

WEB = "https://paperless.example/"
LIMIT = 100


class Recording(FakePaperless):
    """The fake, counting the files it hands out."""

    served: int = 0

    @override
    async def get_document_file(
        self,
        document_id: int,
        version: FileVersion,
        *,
        max_bytes: int,
        version_id: int | None = None,
    ) -> DocumentFile:
        self.served += 1
        return await super().get_document_file(
            document_id, version, max_bytes=max_bytes, version_id=version_id
        )


class Understating(Recording):
    """A Paperless whose metadata names a smaller size than the file really has."""

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        found = await super().get_document_metadata(document_id, version_id=version_id)
        return found.model_copy(update={"original_size": 1, "archive_size": 1})


class Sizeless(Recording):
    """A Paperless that knows an archive version exists but not its size."""

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        found = await super().get_document_metadata(document_id, version_id=version_id)
        return found.model_copy(update={"archive_size": None})


class NoUploadName(Recording):
    """A Paperless that has no name for the uploaded file."""

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        found = await super().get_document_metadata(document_id, version_id=version_id)
        return found.model_copy(update={"original_filename": ""})


def files(fake: FakePaperless, limit: int = LIMIT) -> FileService:
    return FileService(fake, web_url=WEB, max_bytes=limit)


def fake_with(
    *,
    original: bytes = b"%PDF-1.4 fake original",
    archive: bytes | None = b"%PDF-1.4 fake archive",
    mime_type: str = "application/pdf",
    file_name: str | None = None,
) -> Recording:
    state = seeded()
    state.documents.append(
        FakeDocument(
            id=7,
            title="Müller €",
            original=original,
            archive=archive,
            mime_type=mime_type,
            file_name=file_name,
        )
    )
    return Recording(state, user="admin")


async def test_a_thumbnail_comes_with_its_report() -> None:
    fake = Recording(seeded(), user=ADMIN[0])

    delivery = await files(fake).thumbnail(1)

    report = delivery.report
    assert delivery.data == (await fake.get_document_thumbnail(1)).content
    assert (report.document_id, report.kind) == (1, "thumbnail")
    assert (report.mime_type, report.filename) == ("image/webp", "0000001.webp")
    assert report.size_bytes == len(delivery.data or b"")
    assert (report.delivered, report.max_bytes, report.note) == (True, None, None)
    assert report.web_url == "https://paperless.example/documents/1/details"


IN_TRASH = "it is a deleted document or version, so its thumbnail is not shown here."


async def trashed(state: FakeState) -> tuple[int, int, int]:
    """A live document, a deleted one the reader has a view grant on, and a deleted version."""
    writer = FakePaperless(state, user=ADMIN[0], allow_writes=True)
    state.documents.append(FakeDocument(id=70, title="Shared", owner=2, viewers={3}))
    upload = VersionUpload(filename="v.txt", mime_type="text/plain", content=b"v")
    made = await writer.get_task(await writer.upload_document_version(1, upload))
    [version] = made.document_ids
    _ = await writer.delete_version(1, version)
    await writer.delete_document(70)
    return 1, 70, version


@pytest.mark.parametrize("user", [ADMIN[0], READER[0]])
async def test_the_thumbnail_of_what_is_in_the_trash_is_refused_and_that_is_said(
    user: str,
) -> None:
    state = seeded()
    live, document, version = await trashed(state)
    fake = Recording(state, user=user)

    assert (await files(fake).thumbnail(live)).data
    for number in (document, version):
        assert (await fake.get_document_thumbnail(number)).content  # Paperless serves it
        with pytest.raises(NotFound) as raised:
            _ = await files(fake).thumbnail(number)
        assert raised.value.message == f"{number} is in the trash: {IN_TRASH}"
        assert raised.value.hint == TRASHED_HINT


async def test_a_document_deleted_while_its_thumbnail_is_read_is_not_shown() -> None:
    class Deleting(FakePaperless):
        @override
        async def get_document_thumbnail(
            self, document_id: int, *, version_id: int | None = None
        ) -> DocumentFile:
            found = await super().get_document_thumbnail(document_id, version_id=version_id)
            await FakePaperless(self.state, user=ADMIN[0], allow_writes=True).delete_document(1)
            return found

    with pytest.raises(NotFound, match="1 is in the trash"):
        _ = await files(Deleting(seeded(), user=ADMIN[0])).thumbnail(1)


class Versioned(FakePaperless):
    """Document 1 has a version 70 that is not deleted. Failures as told."""

    titles_error: PaperlessMcpError | None = None
    root_error: PaperlessMcpError | None = None
    metadata_error: PaperlessMcpError | None = None
    asked: tuple[str, ...] = ()

    @override
    async def get_document_thumbnail(
        self, document_id: int, *, version_id: int | None = None
    ) -> DocumentFile:
        return await super().get_document_thumbnail(
            1 if document_id == 70 else document_id, version_id=version_id
        )

    @override
    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        self.asked = (*self.asked, f"titles {sorted(document_ids)}")
        if self.titles_error:
            raise self.titles_error
        return await super().get_document_titles(document_ids)

    @override
    async def get_root(self, document_id: int) -> int:
        self.asked = (*self.asked, f"root {document_id}")
        if self.root_error:
            raise self.root_error
        return await super().get_root(1 if document_id == 70 else document_id)

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        self.asked = (*self.asked, f"metadata {document_id}")
        if self.metadata_error:
            raise self.metadata_error
        return FileMetadata(
            original_filename=None,
            original_mime_type="text/plain",
            original_size=1,
            original_checksum="x",
            media_filename=None,
            has_archive_version=False,
            archive_size=None,
            archive_checksum=None,
            archive_media_filename=None,
        )


def versioned() -> Versioned:
    return Versioned(seeded(), user=ADMIN[0])


async def test_a_live_document_takes_one_look_up_and_a_live_version_three_more() -> None:
    document, version = versioned(), versioned()

    assert (await files(document).thumbnail(1)).data
    assert (await files(version).thumbnail(70)).data

    assert document.asked == ("titles [1]",)
    assert version.asked == ("titles [70]", "root 70", "titles [1]", "metadata 70")


async def test_a_live_version_of_a_trashed_document_is_not_shown() -> None:
    fake = versioned()
    await FakePaperless(fake.state, user=ADMIN[0], allow_writes=True).delete_document(1)

    with pytest.raises(NotFound, match="70 is in the trash"):
        _ = await files(fake).thumbnail(70)


@pytest.mark.parametrize("what", ["titles_error", "root_error", "metadata_error"])
@pytest.mark.parametrize(
    "error", [UpstreamTimeout("slow"), PermissionDenied("no"), NotFound("gone")]
)
async def test_a_thumbnail_is_not_shown_where_a_look_up_fails(
    what: str, error: PaperlessMcpError
) -> None:
    fake = versioned()
    if what == "titles_error":
        fake.titles_error = error
    elif what == "root_error":
        fake.root_error = error
    else:
        fake.metadata_error = error

    with pytest.raises(type(error)) as raised:
        _ = await files(fake).thumbnail(70)

    missing = what == "metadata_error" and isinstance(error, NotFound)
    assert raised.value.message == (f"70 is in the trash: {IN_TRASH}" if missing else error.message)


async def test_an_original_within_the_limit_is_delivered_with_its_name_and_type() -> None:
    fake = fake_with(original=b"x" * LIMIT, file_name="Müller.txt", mime_type="text/plain")

    delivery = await files(fake).download(7, "original")

    report = delivery.report
    assert delivery.data == b"x" * LIMIT
    assert (report.document_id, report.kind) == (7, "original")
    assert (report.mime_type, report.filename) == (
        "text/plain; charset=utf-8",
        "2026-11-11 Müller €.txt",
    )
    assert (report.size_bytes, report.delivered, report.max_bytes) == (LIMIT, True, LIMIT)
    assert report.note is None
    assert report.web_url == "https://paperless.example/documents/7/details"


async def test_an_archive_within_the_limit_is_delivered_as_pdf() -> None:
    fake = fake_with(original=b"x" * 500, archive=b"%PDF-archive")

    delivery = await files(fake).download(7, "archive")

    assert delivery.data == b"%PDF-archive"
    assert (delivery.report.kind, delivery.report.mime_type) == ("archive", "application/pdf")
    assert delivery.report.filename == "2026-11-11 Müller €.pdf"
    assert delivery.report.size_bytes == len(b"%PDF-archive")


async def test_a_file_over_the_limit_is_reported_without_being_downloaded() -> None:
    fake = fake_with(original=b"x" * (LIMIT + 1), file_name="Rechnung Käse.pdf")

    delivery = await files(fake).download(7, "original")

    report = delivery.report
    assert delivery.data is None
    assert fake.served == 0
    assert (report.delivered, report.size_bytes, report.max_bytes) == (False, LIMIT + 1, LIMIT)
    assert (report.mime_type, report.filename) == ("application/pdf", "Rechnung Käse.pdf")
    assert report.note is not None
    assert f"{LIMIT} bytes" in report.note
    assert report.web_url in report.note
    assert "get_document_content" in report.note
    assert report.web_url == "https://paperless.example/documents/7/details"


async def test_an_archive_over_the_limit_is_named_after_the_upload_as_pdf() -> None:
    fake = fake_with(original=b"x", archive=b"x" * (LIMIT + 1), file_name="scan.JPG")

    delivery = await files(fake).download(7, "archive")

    assert delivery.data is None
    assert (delivery.report.mime_type, delivery.report.filename) == ("application/pdf", "scan.pdf")
    assert delivery.report.size_bytes == LIMIT + 1


async def test_the_bytes_that_arrive_are_limited_even_if_the_metadata_understates_them() -> None:
    state = seeded()
    state.documents.append(FakeDocument(id=7, title="big", original=b"x" * (LIMIT + 50)))
    fake = Understating(state, user=ADMIN[0])

    delivery = await files(fake).download(7, "original")

    report = delivery.report
    assert delivery.data is None
    assert fake.served == 1
    assert (report.delivered, report.size_bytes) == (False, None)
    assert (report.mime_type, report.filename) == ("application/pdf", "2026-11-11 big.pdf")
    assert report.note is not None
    assert f"{LIMIT} bytes" in report.note


async def test_an_archive_of_unknown_size_is_not_taken_for_the_original_s_size() -> None:
    state = seeded()
    state.documents.append(
        FakeDocument(id=7, title="big", original=b"x", archive=b"y" * (LIMIT + 1))
    )
    fake = Sizeless(state, user=ADMIN[0])

    delivery = await files(fake).download(7, "archive")

    assert delivery.data is None
    assert (delivery.report.delivered, delivery.report.size_bytes) == (False, None)
    assert fake.served == 1


async def test_an_archive_of_unknown_size_within_the_limit_is_delivered_with_its_real_size() -> (
    None
):
    state = seeded()
    state.documents.append(FakeDocument(id=7, title="small", original=b"x", archive=b"%PDF-small"))
    fake = Sizeless(state, user=ADMIN[0])

    delivery = await files(fake).download(7, "archive")

    assert delivery.data == b"%PDF-small"
    assert (delivery.report.delivered, delivery.report.size_bytes) == (True, 10)


@pytest.mark.parametrize("version", ["original", "archive"])
async def test_a_withheld_file_without_an_upload_name_has_no_name(version: FileVersion) -> None:
    state = seeded()
    state.documents.append(
        FakeDocument(id=7, title="big", original=b"x" * (LIMIT + 1), archive=b"y" * (LIMIT + 1))
    )

    delivery = await files(NoUploadName(state, user=ADMIN[0])).download(7, version)

    assert (delivery.report.delivered, delivery.report.filename) == (False, None)


async def test_a_document_without_archive_version_has_no_archive_to_download() -> None:
    fake = fake_with(archive=None, mime_type="text/plain")

    with pytest.raises(NotFound, match="no archive version") as raised:
        _ = await files(fake).download(7, "archive")

    assert raised.value.hint == (
        'Call download_document with kind="original"; it is the only file the document has.'
    )
    assert fake.served == 0
    assert (await files(fake).download(7, "original")).report.delivered


async def test_a_missing_or_hidden_document_is_an_error_before_anything_is_read() -> None:
    reader = Recording(seeded(), user=READER[0])

    with pytest.raises(NotFound):
        _ = await files(reader).download(999, "original")
    with pytest.raises(PermissionDenied):
        _ = await files(reader).download(2, "original")
    with pytest.raises(PermissionDenied):
        _ = await files(reader).thumbnail(2)
    assert reader.served == 0


async def test_an_empty_thumbnail_is_an_error() -> None:
    class Empty(Recording):
        @override
        async def get_document_thumbnail(
            self, document_id: int, *, version_id: int | None = None
        ) -> DocumentFile:
            found = await super().get_document_thumbnail(document_id)
            return found.model_copy(update={"content": b""})

    with pytest.raises(PaperlessMcpError, match="empty thumbnail"):
        _ = await files(Empty(seeded(), user=ADMIN[0])).thumbnail(1)


# --- Mutation-testing additions (PLMCP-21)


async def test_an_original_over_the_limit_keeps_its_own_name_and_type() -> None:
    fake = fake_with(original=b"x" * (LIMIT + 1), file_name="photo.png", mime_type="image/png")

    delivery = await files(fake).download(7, "original")

    assert (delivery.report.filename, delivery.report.mime_type) == ("photo.png", "image/png")


async def test_the_note_on_a_withheld_file_tells_the_administrator_the_setting_to_raise() -> None:
    fake = fake_with(original=b"x" * (LIMIT + 1))

    delivery = await files(fake).download(7, "original")

    assert delivery.report.note is not None
    assert delivery.report.note.endswith(
        "or ask the administrator to raise PAPERLESS_MCP_MAX_DOWNLOAD_BYTES."
    )


async def test_an_archive_over_the_limit_is_a_pdf_whatever_the_original_was() -> None:
    fake = fake_with(
        original=b"x", archive=b"x" * (LIMIT + 1), file_name="photo.png", mime_type="image/png"
    )

    delivery = await files(fake).download(7, "archive")

    assert (delivery.report.filename, delivery.report.mime_type) == ("photo.pdf", "application/pdf")


async def with_a_version(state: FakeState) -> int:
    """The id of a new version of document 1."""
    writer = FakePaperless(state, user=ADMIN[0], allow_writes=True)
    upload = VersionUpload(filename="v.txt", mime_type="text/plain", content=b"v")
    made = await writer.get_task(await writer.upload_document_version(1, upload))
    return made.document_ids[0]


async def test_the_thumbnail_of_a_listed_version_names_it() -> None:
    state = seeded()
    version = await with_a_version(state)
    fake = FakePaperless(state, user=ADMIN[0])

    delivery = await files(fake).thumbnail(1, version_id=1)

    assert delivery.report.version_id == 1
    assert delivery.data == (await fake.get_document_thumbnail(1, version_id=1)).content
    assert (await files(fake).thumbnail(1, version_id=version)).data != delivery.data
    assert (await files(fake).thumbnail(1)).report.version_id is None


async def test_the_thumbnail_of_a_deleted_version_is_refused() -> None:
    state = seeded()
    _, _, version = await trashed(state)

    @final
    class Serving(FakePaperless):
        """Paperless could still answer the image of a version deleted meanwhile."""

        @override
        async def get_document_thumbnail(
            self, document_id: int, *, version_id: int | None = None
        ) -> DocumentFile:
            return await super().get_document_thumbnail(document_id)

    with pytest.raises(NotFound, match=f"version {version}"):
        _ = await files(Serving(state, user=ADMIN[0])).thumbnail(1, version_id=version)
