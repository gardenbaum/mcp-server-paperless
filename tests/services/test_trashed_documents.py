"""Looking into the trash: fields, text, thumbnail, file and parts of an entry (PLMCP-78)."""

import hashlib
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import READER
from fakes.paperless import FakePaperless, FakeState

from mcp_server_paperless.domain.documents import DocumentFile
from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
)
from mcp_server_paperless.domain.files import STILL_READ
from mcp_server_paperless.domain.versions import VersionUpload
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.trash_scan import SKIPPED, TrashScan
from mcp_server_paperless.services.trashed_documents import (
    DOCUMENT_LIMITS,
    NO_CHANGE,
    NO_FILE,
    NO_PERMISSIONS,
    NO_THUMBNAIL,
    RESTORE_FIRST,
    VERSION_LIMITS,
    TrashedDocuments,
)
from services.test_trash import WEB, Setup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from mcp_server_paperless.domain.bulk import ObjectPermissions
    from mcp_server_paperless.domain.documents import DocumentRecord, FileMetadata
    from mcp_server_paperless.domain.permissions import ObjectAccess, PermissionKind
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.domain.trash import TrashLookup

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-54")]

LIMIT = 100


def looking(
    setup: Setup, fake: FakePaperless | None = None, *, cap: int = 1000, limit: int = LIMIT
) -> TrashedDocuments:
    reader = fake or setup.fake
    documents = DocumentService(reader, reader, setup.taxonomy, web_url=WEB)
    scan = TrashScan(reader, reader, setup.taxonomy, cap=cap)
    return TrashedDocuments(reader, reader, reader, documents, scan, web_url=WEB, max_bytes=limit)


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
    with pytest.raises(NotFound, match=f"Not found in the trash: {second}") as raised:
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
    error: Exception | None = None
    fits: int = 0

    @override
    def __init__(self, state: FakeState, *, user: str) -> None:
        super().__init__(state, user=user)
        self.asked: list[tuple[int, int]] = []

    @override
    async def get_trash_permissions(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> ObjectPermissions | None:
        return None

    @override
    async def look_up_in_trash(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> TrashLookup:
        self.asked.append((page, page_size))
        if self.too_large and len(self.asked) == 1:
            message = "too large"
            raise AnswerTooLarge(message)
        if self.error and page_size > self.fits:
            raise self.error
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
    for cap, said in ((1, True), (1000, False)):
        with pytest.raises(NotFound, match="Not found in the trash: 4242") as raised:
            _ = await looking(setup, fake, cap=cap).entry(4242, max_chars=1)
        assert (SKIPPED in (raised.value.hint or "")) is said


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
    async def get_document_thumbnail(
        self, document_id: int, *, version_id: int | None = None
    ) -> DocumentFile:
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
        with pytest.raises(NotFound, match=f"Not found in the trash: {shared}"):
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


@pytest.mark.parametrize(
    ("error", "asked"),
    [
        (UpstreamTimeout("slow"), [(1, 1000), (1, 100)]),
        (UpstreamUnavailable("502"), [(1, 1000), (1, 100)]),
        (UpstreamUnreachable("gone"), [(1, 1000), (1, 100)]),
    ],
)
async def test_an_entry_is_found_in_small_requests_where_a_large_one_does_not_work(
    error: Exception, asked: list[tuple[int, int]]
) -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    fake = Pages(setup.state, user="admin")
    fake.error, fake.fits = error, 100

    found = await looking(setup, fake).entry(one, max_chars=1)

    assert (found.id, fake.asked) == (one, asked)


async def test_a_lookup_that_fails_for_good_says_why() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    at_once = (PermissionDenied("no"), AnswerCompressed("gzip", hint="Check the proxy."))
    for error in (AnswerTooLarge("large"), UpstreamTimeout("slow"), *at_once):
        fake = Pages(setup.state, user="admin")
        fake.error = error
        with pytest.raises(type(error)) as raised:
            _ = await looking(setup, fake).entry(one, max_chars=1)
        if isinstance(error, AnswerTooLarge):
            assert raised.value.hint == "List them page by page with list_trash, without a filter."
        assert error not in at_once or raised.value.hint == error.hint
        assert len(fake.asked) == (1 if error in at_once else 2)


async def test_a_version_says_that_its_fields_are_those_of_its_upload() -> None:
    setup = Setup()
    _, version = await setup.version("Report")

    limits = (await looking(setup).entry(version, max_chars=1)).limits

    assert "title, type, owner and other fields are those of its upload" in limits


async def test_the_grants_of_an_entry_come_with_names() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    [entry] = [d for d in setup.state.trash if d.id == one]
    entry.viewers, entry.editors, entry.view_groups = {3}, {3}, {1}

    found = await looking(setup).entry(one, max_chars=1)

    assert found.permissions
    assert [(r.id, r.name) for r in found.permissions.view_users] == [(3, READER[0])]
    assert [(r.id, r.name) for r in found.permissions.change_users] == [(3, READER[0])]
    assert [(r.id, r.name) for r in found.permissions.view_groups] == [(1, "e2e-group")]
    assert found.permissions.change_groups == ()
    assert found.user_can_change is True
    assert NO_PERMISSIONS not in found.limits


class NoGrants(FakePaperless):
    failure: PaperlessMcpError | None = None

    @override
    async def get_trash_permissions(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> ObjectPermissions | None:
        if self.failure:
            raise self.failure
        return None


@pytest.mark.parametrize(
    "failure", [None, UpstreamTimeout("slow"), PermissionDenied("no"), AnswerTooLarge("large")]
)
async def test_grants_that_could_not_be_read_do_not_lose_the_entry(
    failure: PaperlessMcpError | None,
) -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    fake = NoGrants(setup.state, user="admin")
    fake.failure = failure

    found = await looking(setup, fake).entry(one, max_chars=1)

    assert (found.id, found.permissions) == (one, None)
    assert found.limits == DOCUMENT_LIMITS + NO_PERMISSIONS


async def test_who_may_use_a_version_is_what_its_document_says() -> None:
    setup = Setup()
    document, version = await setup.version("Report")
    [live] = [d for d in setup.state.documents if d.id == document]
    live.viewers, live.editors, live.view_groups = {3}, {3}, {1}

    found = await looking(setup).entry(version, max_chars=1)

    assert found.permissions
    assert [r.name for r in found.permissions.view_users] == [READER[0]]
    assert [r.name for r in found.permissions.change_users] == [READER[0]]
    assert [r.name for r in found.permissions.view_groups] == ["e2e-group"]
    assert (found.user_can_change, found.is_shared_by_requester) == (True, None)
    assert found.limits == VERSION_LIMITS
    assert "permissions and user_can_change are those of its document" in found.limits
    assert "is_shared_by_requester is not known" in found.limits


class NoDocument(FakePaperless):
    """The document of a version cannot be read, or not who may use it."""

    failure: PaperlessMcpError | None = None
    both: bool = True

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        if self.both and self.failure:
            raise self.failure
        return await super().get_document(document_id)

    @override
    async def get_permissions(
        self, kind: PermissionKind, object_ids: Sequence[int]
    ) -> list[ObjectAccess]:
        if self.failure:
            raise self.failure
        return []


@pytest.mark.parametrize("failure", [None, UpstreamTimeout("slow"), PermissionDenied("no")])
async def test_what_could_not_be_read_of_the_document_of_a_version_is_named(
    failure: PaperlessMcpError | None,
) -> None:
    setup = Setup()
    _, version = await setup.version("Report")
    fake = NoDocument(setup.state, user="admin")
    fake.failure = failure

    found = await looking(setup, fake).entry(version, max_chars=1)

    assert (found.id, found.permissions) == (version, None)
    assert found.user_can_change is (True if failure is None else None)
    gap = NO_PERMISSIONS + (NO_CHANGE if failure else "")
    assert found.limits == VERSION_LIMITS + gap


async def test_too_many_requests_do_not_lead_to_smaller_ones() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    fake = Pages(setup.state, user="admin")
    fake.error = UpstreamUnavailable("Paperless is temporarily unavailable.", status=429)
    fake.fits = 100

    with pytest.raises(UpstreamUnavailable):
        _ = await looking(setup, fake).entry(one, max_chars=1)

    assert fake.asked == [(1, 1000)]


class NoNames(FakePaperless):
    """The groups cannot be listed for now, so grants have no names."""

    @override
    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        if kind == "group":
            message = "Paperless could not be reached."
            raise UpstreamUnreachable(message)
        return await super().list_taxonomy(kind)


@pytest.mark.parametrize("version", [False, True])
async def test_grants_without_names_for_want_of_the_groups_are_a_gap(
    version: bool,  # noqa: FBT001 -- a parametrised flag, not a switch
) -> None:
    setup = Setup()
    one = (await setup.version("Report"))[1] if version else await setup.trashed("Doc")
    fake = NoNames(setup.state, user="admin")
    setup.taxonomy = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=0.0)

    found = await looking(setup, fake).entry(one, max_chars=1)

    assert (found.id, found.permissions) == (one, None)
    assert found.limits == (VERSION_LIMITS if version else DOCUMENT_LIMITS) + NO_PERMISSIONS


async def failing(call: Awaitable[object]) -> NotFound:
    with pytest.raises(NotFound) as raised:
        _ = await call
    return raised.value


async def test_a_missing_file_of_a_document_in_the_trash_is_not_claimed_to_be_missing() -> None:
    """Paperless tells no metadata of it, so 404 has more than one cause."""
    setup = Setup()
    one = await setup.trashed("Doc")
    setup.state.missing = {(one, "original"), (one, "archive"), (one, "thumbnail")}
    service = looking(setup)

    errors = [
        await failing(service.download(one, "original")),
        await failing(service.download(one, "archive")),
        await failing(service.part(one, "original", offset=0)),
        await failing(service.thumbnail(one)),
    ]

    assert {(e.message, e.hint) for e in errors} == {(NO_FILE, RESTORE_FIRST)}
    assert "does not say why" in NO_FILE
    # What is in the list is still readable.
    assert (await service.entry(one, text=True, max_chars=5)).id == one


async def test_a_missing_file_of_a_deleted_version_is_named_as_for_live_documents() -> None:
    setup = Setup()
    document, version = await setup.version("Report")
    [entry] = [d for d in setup.state.trash if d.id == version]
    entry.archive = b"archive"
    setup.state.missing = {(version, "original"), (version, "thumbnail")}
    service = looking(setup)

    original = await failing(service.download(version, "original"))
    part = await failing(service.part(version, "original", offset=0))
    thumbnail = await failing(service.thumbnail(version))
    archive = await service.download(version, "archive")
    setup.state.missing.add((version, "archive"))
    both = await failing(service.download(version, "original"))

    said = f"Paperless cannot find the original of document {document} (version {version})."
    assert (original.message, original.hint) == (
        said,
        "Ask for the archive version, which is there.",
    )
    assert (part.message, part.hint) == (original.message, original.hint)
    assert archive.data == b"archive"
    assert both.hint == STILL_READ
    assert thumbnail.message == (
        f"Paperless cannot find the thumbnail of document {document} (version {version})."
    )
    assert thumbnail.hint == NO_THUMBNAIL


async def test_nothing_is_claimed_of_a_version_whose_metadata_cannot_be_read() -> None:
    setup = Setup()
    _, version = await setup.version("Report")
    setup.state.missing = {(version, "original"), (version, "thumbnail")}

    class NoMetadata(FakePaperless):
        @override
        async def get_document_metadata(
            self, document_id: int, *, version_id: int | None = None
        ) -> FileMetadata:
            message = "Paperless could not be reached."
            raise UpstreamUnreachable(message)

    service = looking(setup, NoMetadata(setup.state, user="admin"))

    for call in (service.download(version, "original"), service.thumbnail(version)):
        error = await failing(call)
        assert (error.message, error.hint) == (NO_FILE, RESTORE_FIRST)


async def test_a_listed_file_of_a_version_that_is_not_sent_is_not_called_missing() -> None:
    setup = Setup()
    _, version = await setup.version("Report")

    error = await failing(
        looking(setup, NoFile(setup.state, user="admin")).download(version, "original")
    )

    assert (error.message, error.hint) == (NO_FILE, RESTORE_FIRST)


async def test_entries_without_names_are_read() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    _, version = await setup.version("Report")
    setup.state.nameless = {one, version}
    for entry in setup.state.trash:
        entry.archive = None
    service = looking(setup)

    for number in (one, version):
        found = await service.entry(number, max_chars=1)
        assert (found.id, found.archived_file_name) == (number, None)
        assert (await service.download(number, "original")).data
