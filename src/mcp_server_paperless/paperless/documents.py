"""DocumentReader on ``GET /api/documents/`` and DocumentWriter on ``PATCH /api/documents/{id}/``.

Every search names its ``fields``, so Paperless never sends the OCR content, notes or
permissions. Full-text and more-like searches add ``__search_hit__`` to each row.
"""

import asyncio
import contextlib
import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field, JsonValue, RootModel

from mcp_server_paperless.domain.documents import (
    CustomFieldValue,
    DocumentFile,
    DocumentHit,
    DocumentPage,
    DocumentPatch,
    DocumentRecord,
    DocumentText,
    DocumentUpload,
    DocumentVersion,
    FileMetadata,
    HistoryEntry,
    Note,
    SignedIn,
    Suggestions,
)
from mcp_server_paperless.domain.duplicates import DuplicateRecord
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    FeatureUnavailable,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.files import missing_file, not_sent
from mcp_server_paperless.domain.selection import DocumentRow
from mcp_server_paperless.domain.versions import VersionPage, VersionRow
from mcp_server_paperless.paperless.client import MAY_BE_APPLIED, opened
from mcp_server_paperless.paperless.disposition import filename
from mcp_server_paperless.paperless.pagination import Page
from mcp_server_paperless.paperless.queued import Queued, TaskWaiter, not_applied

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Sequence

    from mcp_server_paperless.domain.documents import DocumentQuery, FileVersion
    from mcp_server_paperless.domain.files import OpenedFile
    from mcp_server_paperless.domain.selection import ReadField
    from mcp_server_paperless.domain.tasks import Unconfirmed
    from mcp_server_paperless.domain.versions import VersionUpload
    from mcp_server_paperless.paperless.client import PaperlessClient, Params

PATH = "/api/documents/"
UPLOAD_PATH = "/api/documents/post_document/"
MERGE: Final = "merge"
MERGE_AS_VERSIONS_PATH = "/api/documents/merge_as_versions/"
# Most bytes of a thumbnail read; real ones are 2 KB to 30 KB WebP, so this is generous.
THUMBNAIL_LIMIT = 4 * 1024 * 1024
NO_THUMBNAIL_HINT: Final = (
    "Paperless makes it anew when the document is reprocessed, if it still has the file."
)
FIELDS = (
    "id,title,created,added,correspondent,document_type,storage_path,tags,custom_fields,"
    "user_can_change"
)
# A single document without its content (the OCR text can be a million characters).
DETAIL_FIELDS = (
    "id,title,created,added,modified,archive_serial_number,owner,correspondent,document_type,"
    "storage_path,tags,custom_fields,original_file_name,mime_type,page_count,"
    "notes,versions,user_can_change"
)
# Where a field of a row has another name in Paperless.
SENT_AS: Final[dict[ReadField, str]] = {"version_count": "versions"}
# Seconds for an answer with many documents.
MANY_TIMEOUT: Final = 60.0
# The most of a document's history, which holds every corrected text, that is read.
HISTORY_BYTES: Final = 64 * 1024 * 1024
# The text of one version, and what identifies it.
TEXT_FIELDS = "content,title,versions,user_can_change"


class _Value(BaseModel):
    field: int
    value: JsonValue


class _Hit(BaseModel):
    highlights: str | None = None
    note_highlights: str | None = None


class _Row(BaseModel):
    id: int
    title: str
    created: date
    added: datetime
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    tags: list[int]
    custom_fields: list[_Value]
    user_can_change: bool | None = None
    search_hit: _Hit | None = Field(default=None, alias="__search_hit__")


class _Version(BaseModel):
    id: int
    added: datetime
    version_label: str | None = None
    checksum: str | None = None
    is_root: bool


class _VersionDeleted(BaseModel):
    """What deleting a version answers: ``current_version_id`` is the newest one left."""

    current_version_id: int


class _User(BaseModel):
    username: str


class _Note(BaseModel):
    id: int
    note: str
    created: datetime | None = None
    user: _User | None = None


class _Chosen(BaseModel):
    """A row with the fields that were asked for."""

    id: int
    title: str | None = None
    created: date | None = None
    added: datetime | None = None
    modified: datetime | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags: list[int] | None = None
    custom_fields: list[CustomFieldValue] | None = None
    archive_serial_number: int | None = None
    owner: int | None = None
    original_file_name: str | None = None
    mime_type: str | None = None
    page_count: int | None = None
    notes: list[_Note] | None = None
    versions: list[_Version] | None = None
    user_can_change: bool | None = None


class _Detail(BaseModel):
    id: int
    title: str
    created: date
    added: datetime
    modified: datetime
    archive_serial_number: int | None
    owner: int | None
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    tags: list[int]
    custom_fields: list[_Value]
    original_file_name: str | None
    mime_type: str
    page_count: int | None
    notes: list[_Note]
    versions: list[_Version]


class _Readable(_Detail):
    """What a read of one document sends beyond an update's answer."""

    user_can_change: bool


class _Title(BaseModel):
    id: int
    title: str


class _Versions(BaseModel):
    id: int
    versions: list[_Version]


class _VersionRow(_Title):
    versions: list[_Version]


class _Duplicate(_Title):
    deleted_at: datetime | None


class _Duplicates(BaseModel):
    duplicate_documents: list[_Duplicate]


class _Checksum(BaseModel):
    original_checksum: str | None


class _Root(BaseModel):
    root_id: int


class _Text(BaseModel):
    content: str
    title: str
    versions: list[_Version]
    user_can_change: bool


class _Pages(BaseModel):
    page_count: int | None
    versions: list[_Version]


class _SignedIn(BaseModel):
    id: int
    is_superuser: bool = False


class _Rights(BaseModel):
    user: _SignedIn
    permissions: list[str] | None = None


class _Content(BaseModel):
    content: str


class _HistoryRow(BaseModel):
    id: int
    timestamp: datetime
    action: str
    changes: dict[str, JsonValue]
    actor: _User | None = None


class _Metadata(BaseModel):
    original_filename: str | None
    original_mime_type: str
    original_size: int | None
    original_checksum: str
    media_filename: str | None
    has_archive_version: bool
    archive_size: int | None = None
    archive_checksum: str | None = None
    archive_media_filename: str | None = None


class _Suggestions(BaseModel):
    correspondents: list[int]
    tags: list[int]
    document_types: list[int]
    storage_paths: list[int]
    dates: list[str]


class _TaskId(RootModel[str]):
    """The answer of ``post_document``: the UUID of the consumption task, as a JSON string."""


class _Notes(RootModel[list[_Note]]):
    pass


class _SaveError(BaseModel):
    """What the notes endpoint answers with status 200 when it could not save a note."""

    error: str


class _Saved(RootModel[list[_Note] | _SaveError]):
    pass


class _History(RootModel[list[_HistoryRow]]):
    pass


def _csv(ids: tuple[int, ...]) -> str | None:
    return ",".join(map(str, ids)) or None


def _day(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _flag(value: bool | None) -> str | None:  # noqa: FBT001 -- a tri-state filter, not a switch
    return None if value is None else str(value).lower()


def params(query: DocumentQuery) -> dict[str, str | int]:
    """The Paperless query parameters of ``query``; unset filters are left out."""
    candidates: dict[str, str | int | None] = {
        "query": query.query,
        "more_like_id": query.more_like,
        "text": query.text,
        "title_search": query.title_search,
        "title__icontains": query.title_contains,
        "content__icontains": query.content_contains,
        "correspondent__id": query.correspondent,
        "correspondent__isnull": _flag(query.correspondent_isnull),
        "correspondent__id__in": _csv(query.correspondent_in),
        "correspondent__id__none": _csv(query.correspondent_none),
        "document_type__id": query.document_type,
        "document_type__isnull": _flag(query.document_type_isnull),
        "document_type__id__in": _csv(query.document_type_in),
        "document_type__id__none": _csv(query.document_type_none),
        "storage_path__id": query.storage_path,
        "storage_path__isnull": _flag(query.storage_path_isnull),
        "storage_path__id__in": _csv(query.storage_path_in),
        "storage_path__id__none": _csv(query.storage_path_none),
        "tags__id__all": _csv(query.tags_all),
        "tags__id__in": _csv(query.tags_any),
        "tags__id__none": _csv(query.tags_none),
        "is_tagged": _flag(query.tagged),
        "archive_serial_number": query.archive_serial_number,
        "archive_serial_number__gt": query.asn_gt,
        "archive_serial_number__lt": query.asn_lt,
        "archive_serial_number__isnull": _flag(query.asn_isnull),
        "created__date__gte": _day(query.created_from),
        "created__date__lte": _day(query.created_to),
        "created__date__gt": _day(query.created_gt),
        "created__date__lt": _day(query.created_lt),
        "created__year": query.created_year,
        "created__month": query.created_month,
        "created__day": query.created_day,
        "added__date__gte": _day(query.added_from),
        "added__date__lte": _day(query.added_to),
        "added__date__gt": _day(query.added_gt),
        "added__date__lt": _day(query.added_lt),
        "modified__date__gt": _day(query.modified_gt),
        "modified__date__lt": _day(query.modified_lt),
        "owner__id": query.owner,
        "owner__id__in": _csv(query.owner_in),
        "owner__id__none": _csv(query.owner_none),
        "owner__isnull": _flag(query.owner_isnull),
        "shared_by__id": query.shared_by,
        "is_in_inbox": _flag(query.inbox),
        "mime_type": query.mime_type,
        "has_duplicates": _flag(query.has_duplicates),
        "custom_fields__icontains": query.custom_fields_icontains,
        "custom_fields__id__all": _csv(query.custom_fields_all),
        "custom_fields__id__in": _csv(query.custom_fields_in),
        "custom_fields__id__none": _csv(query.custom_fields_none),
        "has_custom_fields": _flag(query.has_custom_fields),
        "custom_field_query": (
            None if query.custom_field_query is None else json.dumps(query.custom_field_query)
        ),
        "checksum__iexact": query.checksum,
        "ordering": query.ordering,
        "id__in": _csv(query.ids),
    }
    return {name: value for name, value in candidates.items() if value is not None}


def unseen_document(document: int) -> NotFound:
    """The error for a ``more_like`` document that is missing or hidden from the user."""
    message = f"No document {document} exists that this user may see."
    return NotFound(message, hint="Take the id from a search_documents row.")


def unseen_note(document: int, note: int) -> NotFound:
    """The error for a note that is not on the document (Paperless answers 404 for both)."""
    message = f"No note {note} exists on document {document} that this user may see."
    return NotFound(message, hint="List the notes with get_document and include notes.")


def unseen_version(document: int, version: int) -> NotFound:
    """The error for a version that is not on the document (Paperless answers 404 for both)."""
    message = f"No version {version} exists on document {document} that this user may see."
    return NotFound(message, hint="List the versions with list_document_versions.")


def _unseen(document: int, version: int | None) -> NotFound:
    """The error for a file: of the named version, or of the document if none is named."""
    return unseen_document(document) if version is None else unseen_version(document, version)


def _of_version(version_id: int | None) -> dict[str, int]:
    """The query parameter that names a version; none for the newest."""
    return {} if version_id is None else {"version": version_id}


def beyond_last_page(page: int) -> NotFound:
    """The error for a page past the end of the results."""
    message = f"Page {page} is beyond the last page of the results."
    return NotFound(message, hint="Search with page=1; count and next_page tell how far it goes.")


def _note(row: _Note) -> Note:
    return Note(
        id=row.id, note=row.note, created=row.created, user=row.user.username if row.user else None
    )


def _entry(row: _HistoryRow) -> HistoryEntry:
    """An audit log entry; ``content`` holds the OCR text, which never leaves the adapter."""
    return HistoryEntry(
        id=row.id,
        timestamp=row.timestamp,
        action=row.action,
        actor=row.actor.username if row.actor else None,
        changes={name: change for name, change in row.changes.items() if name != "content"},
    )


def _hit(row: _Row) -> DocumentHit:
    return DocumentHit(
        id=row.id,
        title=row.title,
        created=row.created,
        added=row.added,
        correspondent=row.correspondent,
        document_type=row.document_type,
        storage_path=row.storage_path,
        tags=tuple(row.tags),
        custom_fields=tuple(
            CustomFieldValue(field=v.field, value=v.value) for v in row.custom_fields
        ),
        highlights=(
            (row.search_hit.highlights or row.search_hit.note_highlights or None)
            if row.search_hit
            else None
        ),
        user_can_change=row.user_can_change,
    )


def _page(found: Page[_Row]) -> DocumentPage:
    return DocumentPage(
        count=found.count,
        next_page=found.next_page,
        hits=tuple(_hit(row) for row in found.results),
    )


def _chosen(row: _Chosen) -> DocumentRow:
    """A row as it was asked for: versions are only counted."""
    values = row.model_dump(exclude={"notes", "versions"})
    return DocumentRow.model_validate(
        {
            **values,
            "notes": None if row.notes is None else tuple(_note(note) for note in row.notes),
            "version_count": None if row.versions is None else len(row.versions),
        }
    )


def _version(row: _Version) -> DocumentVersion:
    return DocumentVersion(
        id=row.id,
        added=row.added,
        version_label=row.version_label,
        is_root=row.is_root,
        checksum=row.checksum,
    )


def _record(row: _Detail, *, can_change: bool | None) -> DocumentRecord:
    return DocumentRecord(
        id=row.id,
        title=row.title,
        created=row.created,
        added=row.added,
        modified=row.modified,
        archive_serial_number=row.archive_serial_number,
        owner=row.owner,
        correspondent=row.correspondent,
        document_type=row.document_type,
        storage_path=row.storage_path,
        tags=tuple(row.tags),
        custom_fields=tuple(
            CustomFieldValue(field=v.field, value=v.value) for v in row.custom_fields
        ),
        original_file_name=row.original_file_name,
        mime_type=row.mime_type,
        page_count=row.page_count,
        notes_count=len(row.notes),
        versions=tuple(_version(v) for v in row.versions),
        user_can_change=can_change,
    )


class PaperlessDocumentReader:
    """``DocumentReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        """See ``DocumentReader.search_documents``.

        Paperless answers 403 to a ``more_like_id`` the user may not see or that does not
        exist, and 404 to a page beyond the last one.
        """
        sent: Params = {**params(query), "fields": FIELDS, "page": page, "page_size": page_size}
        try:
            found = await self._client.get(PATH, Page[_Row], params=sent)
        except PermissionDenied:
            if query.more_like is None:
                raise
            raise unseen_document(query.more_like) from None
        except NotFound:
            raise beyond_last_page(page) from None
        return _page(found)

    async def select_documents(
        self, query: DocumentQuery, *, limit: int, max_bytes: int
    ) -> DocumentPage:
        """See ``DocumentReader.select_documents``."""
        sent: Params = {**params(query), "fields": FIELDS, "page": 1, "page_size": limit}
        try:
            found = await self._bounded(Page[_Row], sent, max_bytes)
        except PermissionDenied:
            if query.more_like is None:
                raise
            raise unseen_document(query.more_like) from None
        return _page(found)

    async def read_rows(
        self, document_ids: Sequence[int], fields: Sequence[ReadField], *, max_bytes: int
    ) -> tuple[DocumentRow, ...]:
        """See ``DocumentReader.read_rows``."""
        if not document_ids:
            return ()
        sent: Params = {
            "id__in": ",".join(map(str, document_ids)),
            "fields": ",".join(["id", *(SENT_AS.get(f, f) for f in fields)]),
            "page": 1,
            "page_size": len(document_ids),
        }
        found = await self._bounded(Page[_Chosen], sent, max_bytes)
        return tuple(_chosen(row) for row in found.results)

    async def _bounded[M: BaseModel](self, model: type[M], sent: Params, max_bytes: int) -> M:
        return await self._client.read_json(
            PATH, model, params=sent, max_bytes=max_bytes, timeout=MANY_TIMEOUT
        )

    async def get_document(self, document_id: int) -> DocumentRecord:
        """See ``DocumentReader.get_document``.

        Paperless answers 404 to a document that does not exist and to one the user may not see.
        """
        row = await self._one(document_id, "", _Readable, {"fields": DETAIL_FIELDS})
        return _record(row, can_change=row.user_can_change)

    async def get_document_content(
        self, document_id: int, *, version_id: int | None = None
    ) -> DocumentText:
        """See ``DocumentReader.get_document_content``.

        Paperless answers the text of a version in the trash, too, so the version has to be among
        those the same answer lists.
        """
        params: Params = {"fields": TEXT_FIELDS, **_of_version(version_id)}
        try:
            row = await self._one(document_id, "", _Text, params)
        except NotFound:
            raise _unseen(document_id, version_id) from None
        listed = [version.id for version in row.versions] or [document_id]
        if version_id is not None and version_id not in listed:
            raise unseen_version(document_id, version_id)
        return DocumentText(
            id=document_id,
            title=row.title,
            version_id=listed[0] if version_id is None else version_id,
            versions=tuple(_version(v) for v in row.versions),
            user_can_change=row.user_can_change,
            content=row.content,
        )

    async def get_version_pages(self, document_id: int, version_id: int) -> int | None:
        """See ``DocumentReader.get_version_pages``; listed versions only, as for the text."""
        params: Params = {"fields": "page_count,versions", **_of_version(version_id)}
        try:
            row = await self._one(document_id, "", _Pages, params)
        except NotFound:
            raise _unseen(document_id, version_id) from None
        if version_id not in ([version.id for version in row.versions] or [document_id]):
            raise unseen_version(document_id, version_id)
        return row.page_count

    async def may_delete(self, owners: Sequence[int | None]) -> bool | None:
        """See ``DocumentReader.may_delete``: the permissions ``/api/ui_settings/`` lists."""
        try:
            rights = await self._client.get("/api/ui_settings/", _Rights)
        except PaperlessMcpError:
            return None
        if rights.user.is_superuser:
            return True
        if any(owner not in {None, rights.user.id} for owner in owners):
            return False
        return None if rights.permissions is None else "delete_document" in rights.permissions

    async def get_signed_in(self) -> SignedIn | None:
        """See ``DocumentReader.get_signed_in``: ``/api/ui_settings/`` names the user."""
        try:
            rights = await self._client.get("/api/ui_settings/", _Rights)
        except PermissionDenied:
            return None
        return SignedIn(
            id=rights.user.id,
            is_superuser=rights.user.is_superuser,
            permissions=None if rights.permissions is None else tuple(rights.permissions),
        )

    async def list_document_notes(self, document_id: int) -> tuple[Note, ...]:
        """See ``DocumentReader.list_document_notes``."""
        found = await self._one(document_id, "notes/", _Notes)
        return tuple(_note(row) for row in found.root)

    async def list_document_history(self, document_id: int) -> tuple[HistoryEntry, ...]:
        """See ``DocumentReader.list_document_history``.

        The endpoint answers a bare list although the schema says paginated, and 400 "Audit log
        is disabled" when the instance runs with PAPERLESS_AUDIT_LOG_ENABLED=false (the only
        400 this endpoint has). It holds every corrected text twice, and Paperless ignores the
        paging the schema has.
        """
        try:
            found = await self._client.read_json(
                f"{PATH}{document_id}/history/",
                _History,
                max_bytes=HISTORY_BYTES,
                timeout=MANY_TIMEOUT,
                compressed=True,
            )
        except NotFound:
            raise unseen_document(document_id) from None
        except AnswerTooLarge:
            message = (
                f"The history of document {document_id} is over 64 MiB, because Paperless sends "
                "every corrected text with it."
            )
            hint = "Leave history out of include, or look at it in the Paperless web UI."
            raise AnswerTooLarge(message, hint=hint) from None
        except Validation:
            message = "The Paperless audit log is disabled on this instance."
            raise FeatureUnavailable(message, hint="Leave history out of include.") from None
        return tuple(_entry(row) for row in found.root)

    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        """See ``DocumentReader.get_document_metadata``."""
        try:
            row = await self._one(document_id, "metadata/", _Metadata, _of_version(version_id))
        except NotFound:
            raise _unseen(document_id, version_id) from None
        return FileMetadata.model_validate(row.model_dump())

    async def get_document_suggestions(self, document_id: int) -> Suggestions:
        """See ``DocumentReader.get_document_suggestions``."""
        row = await self._one(document_id, "suggestions/", _Suggestions)
        return Suggestions(
            correspondents=tuple(row.correspondents),
            tags=tuple(row.tags),
            document_types=tuple(row.document_types),
            storage_paths=tuple(row.storage_paths),
            dates=tuple(row.dates),
        )

    async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
        """See ``DocumentReader.get_document_thumbnail``.

        Paperless sends WebP. A document the user may not see is answered 403 (text/html),
        a missing one 404, as for the other sub-resources.
        """
        try:
            found = await self._file(document_id, "thumb/", THUMBNAIL_LIMIT)
        except NotFound:
            # Says so itself if the document does not exist.
            _ = await self.get_document_metadata(document_id)
            message = f"Paperless cannot find the thumbnail of document {document_id}."
            raise NotFound(message, hint=NO_THUMBNAIL_HINT) from None
        if found.truncated:
            message = f"Paperless sent an unexpected response for the thumbnail of {document_id}."
            raise PaperlessMcpError(message)
        return found

    async def get_document_file(
        self,
        document_id: int,
        version: FileVersion,
        *,
        max_bytes: int,
        version_id: int | None = None,
    ) -> DocumentFile:
        """See ``DocumentReader.get_document_file``.

        ``original=false`` is the archive version; Paperless answers the original for it when
        the document has none. The file name is the formatted one Paperless proposes (date,
        correspondent and title), not the name of the upload.
        """
        params: Params = {
            "original": "true" if version == "original" else "false",
            **_of_version(version_id),
        }
        try:
            return await self._file(document_id, "download/", max_bytes, params)
        except NotFound:
            raise await self._not_sent(document_id, version, version_id) from None

    @contextlib.asynccontextmanager
    async def open_document_file(
        self, document_id: int, version: FileVersion, *, version_id: int | None = None
    ) -> AsyncGenerator[OpenedFile]:
        """See ``DocumentReader.open_document_file``.

        Paperless ignores a ``Range`` header and compresses its answers, so there is no part to
        ask for and often no ``Content-Length`` (probed on 3.5.1).
        """
        params: Params = {
            "original": "true" if version == "original" else "false",
            **_of_version(version_id),
        }
        async with contextlib.AsyncExitStack() as stack:
            try:
                response = await stack.enter_async_context(
                    self._client.stream("GET", f"{PATH}{document_id}/download/", params=params)
                )
            except NotFound:
                raise await self._not_sent(document_id, version, version_id) from None
            yield opened(response)

    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        """See ``DocumentReader.get_document_titles``; ``id__in`` leaves hidden ids out."""
        wanted = sorted(set(document_ids))
        if not wanted:
            return {}
        sent: Params = {
            "id__in": ",".join(map(str, wanted)),
            "fields": "id,title",
            "page_size": len(wanted),
        }
        found = await self._client.get(PATH, Page[_Title], params=sent)
        return {row.id: row.title for row in found.results}

    async def get_newest_versions(self, document_ids: Collection[int]) -> dict[int, int]:
        """See ``DocumentReader.get_newest_versions``; Paperless lists the newest version first."""
        wanted = sorted(set(document_ids))
        if not wanted:
            return {}
        sent: Params = {
            "id__in": ",".join(map(str, wanted)),
            "fields": "id,versions",
            "page_size": len(wanted),
        }
        found = await self._client.get(PATH, Page[_Versions], params=sent)
        return {row.id: row.versions[0].id if row.versions else row.id for row in found.results}

    async def get_root(self, document_id: int) -> int:
        """See ``DocumentReader.get_root``; Paperless answers 403 to a hidden document."""
        try:
            return (await self._one(document_id, "root/", _Root)).root_id
        except PermissionDenied:
            raise unseen_document(document_id) from None

    async def document_exists(self, document_id: int) -> bool:
        """See ``DocumentReader.document_exists``; 403 is a hidden one, only 404 none."""
        try:
            _ = await self._one(document_id, "root/", _Root)
        except PermissionDenied:
            return True
        except NotFound:
            return False
        return True

    async def get_duplicates(self, document_id: int) -> tuple[DuplicateRecord, ...]:
        """See ``DocumentReader.get_duplicates``; filled on a single read only, not in lists."""
        row = await self._one(document_id, "", _Duplicates, {"fields": "duplicate_documents"})
        return tuple(
            DuplicateRecord(id=d.id, title=d.title, deleted_at=d.deleted_at)
            for d in row.duplicate_documents
        )

    async def get_version_checksum(self, document_id: int, version_id: int) -> str | None:
        """See ``DocumentReader.get_version_checksum``.

        Nothing else is read: sizes and file names are null when the file is missing.
        """
        try:
            row = await self._one(document_id, "metadata/", _Checksum, _of_version(version_id))
        except NotFound:
            raise unseen_version(document_id, version_id) from None
        return row.original_checksum

    async def list_versions(
        self, *, page: int, page_size: int, recent: bool = False
    ) -> VersionPage:
        """See ``DocumentReader.list_versions``; the id breaks ties, so pages do not overlap."""
        sent: Params = {
            "fields": "id,title,versions",
            "ordering": "-modified,-id" if recent else "-id",
            "page": page,
            "page_size": page_size,
        }
        try:
            found = await self._client.get(PATH, Page[_VersionRow], params=sent)
        except NotFound:
            raise beyond_last_page(page) from None
        return VersionPage(
            count=found.count,
            next_page=found.next_page,
            rows=tuple(
                VersionRow(
                    id=row.id, title=row.title, versions=tuple(_version(v) for v in row.versions)
                )
                for row in found.results
            ),
        )

    async def _not_sent(
        self, document_id: int, version: FileVersion, version_id: int | None
    ) -> PaperlessMcpError:
        """Why a file is a 404: no such document or version, or Paperless cannot find the file."""
        try:
            facts = await self.get_document_metadata(document_id, version_id=version_id)
        except NotFound as unseen:
            return unseen
        if not facts.lacks(version):
            return not_sent(document_id)
        return missing_file(document_id, version, facts, version_id)

    async def _file(
        self, document_id: int, tail: str, max_bytes: int, params: Params | None = None
    ) -> DocumentFile:
        found = await self._client.download(
            f"{PATH}{document_id}/{tail}", params=params, max_bytes=max_bytes
        )
        return DocumentFile(
            content=found.content,
            mime_type=found.headers.get("Content-Type", "application/octet-stream"),
            filename=filename(found.headers.get("Content-Disposition")),
            truncated=found.truncated,
        )

    async def _one[M: BaseModel](
        self, document_id: int, tail: str, model: type[M], params: Params | None = None
    ) -> M:
        try:
            return await self._client.get(f"{PATH}{document_id}/{tail}", model, params=params)
        except NotFound:
            raise unseen_document(document_id) from None


class PaperlessDocumentWriter:
    """``DocumentWriter`` over the Paperless REST API."""

    def __init__(
        self,
        client: PaperlessClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Write through ``client``; ``sleep`` paces the wait for a queued merge."""
        self._client: PaperlessClient = client
        self._waiter: TaskWaiter = TaskWaiter(client, sleep=sleep)

    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        """See ``DocumentWriter.update_document``.

        Sends only the fields ``patch`` sets. The answer to a PATCH is the whole document, OCR
        text included, unless ``fields`` limits it (it does on 3.4.2, although the schema does
        not declare the parameter). Paperless answers 404 to a document that does not exist
        or that the user may not see, and 403 to a user without the change permission.
        """
        body = patch.model_dump(mode="json", exclude_unset=True)
        try:
            row = await self._client.call(
                "PATCH",
                f"{PATH}{document_id}/",
                _Detail,
                params={"fields": DETAIL_FIELDS},
                json=body,
            )
        except NotFound:
            raise unseen_document(document_id) from None
        return _record(row, can_change=None)

    async def set_document_text(self, document_id: int, version_id: int, content: str) -> str:
        """See ``DocumentWriter.set_document_text``.

        Always names the version, also the newest: a version added meanwhile would get the text.
        ``fields`` limits what a PATCH writes, not only its answer: without ``content`` Paperless
        answers 200 and leaves the text of the root version as it is.
        """
        try:
            row = await self._client.call(
                "PATCH",
                f"{PATH}{document_id}/",
                _Content,
                params={"fields": "id,content", "version": version_id},
                json={"content": content},
            )
        except NotFound:
            raise unseen_version(document_id, version_id) from None
        return row.content

    async def add_note(self, document_id: int, text: str) -> Note:
        """See ``DocumentWriter.add_note``.

        Paperless answers 200 with every note of the document, the new one last (picked as the
        newest with the text that was sent, without the whitespace around it, which Paperless
        drops; others may add notes meanwhile, and an answer without it is unusable), or with
        ``{"error": ...}`` and status 200 when it could not save it. A 403 means the global note
        permission is missing, the document is hidden, or the user may not change it.
        """
        try:
            answer = await self._client.call(
                "POST", f"{PATH}{document_id}/notes/", _Saved, json={"note": text}
            )
        except NotFound:
            raise unseen_document(document_id) from None
        if isinstance(answer.root, _SaveError):
            message = "Paperless could not save the note."
            raise Validation(message, hint="Check the Paperless log for the reason.")
        mine = [row for row in answer.root if row.note.strip() == text.strip()]
        if not mine:
            message = f"Paperless sent an unexpected response for POST {PATH}{document_id}/notes/."
            raise PaperlessMcpError(message, hint=MAY_BE_APPLIED)
        return _note(max(mine, key=lambda row: row.id))

    async def delete_note(self, document_id: int, note_id: int) -> None:
        """See ``DocumentWriter.delete_note``.

        Not retried: a repeated DELETE would answer 404 after the first one succeeded.
        Paperless answers 404 both to a missing document and to a note that is not on it.
        """
        try:
            _ = await self._client.request(
                "DELETE", f"{PATH}{document_id}/notes/", params={"id": note_id}, retry=False
            )
        except NotFound:
            raise unseen_note(document_id, note_id) from None

    async def delete_document(self, document_id: int) -> None:
        """See ``DocumentWriter.delete_document``.

        Paperless 3 moves the document to the trash (204). Not retried, like ``delete_note``.
        A document the user may not see is answered 404, one they may see but not delete 403.
        """
        try:
            _ = await self._client.request("DELETE", f"{PATH}{document_id}/", retry=False)
        except NotFound:
            raise unseen_document(document_id) from None

    async def upload_document(self, upload: DocumentUpload) -> str:
        """See ``DocumentWriter.upload_document``.

        One multipart POST to ``post_document``: the file as ``document`` and the metadata as
        form fields, tags repeated and custom fields as a JSON object of field id to value.
        Sent once: the client never retries a POST, and a retry after a lost answer could create
        the document twice.
        """
        form: dict[str, str | list[str]] = {}
        if upload.title is not None:
            form["title"] = upload.title
        if upload.created is not None:
            form["created"] = upload.created.isoformat()
        numbers = {
            "correspondent": upload.correspondent,
            "document_type": upload.document_type,
            "storage_path": upload.storage_path,
            "archive_serial_number": upload.archive_serial_number,
        }
        form.update({name: str(value) for name, value in numbers.items() if value is not None})
        if upload.tags:
            form["tags"] = [str(tag) for tag in upload.tags]
        if upload.custom_fields:
            values = {str(item.field): item.value for item in upload.custom_fields}
            form["custom_fields"] = json.dumps(values, separators=(",", ":"))
        answer = await self._client.call(
            "POST",
            UPLOAD_PATH,
            _TaskId,
            data=form,
            files={"document": (upload.filename, upload.content, upload.mime_type)},
        )
        return answer.root

    async def upload_document_version(self, document_id: int, upload: VersionUpload) -> str:
        """See ``DocumentWriter.upload_document_version``.

        One multipart POST to ``update_version``: the file as ``document`` and the label as
        ``version_label``. Paperless answers 404 to a document that does not exist, and 403 to one
        the user may not see or may not change. Sent once, like an upload.
        """
        form = {} if upload.label is None else {"version_label": upload.label}
        try:
            answer = await self._client.call(
                "POST",
                f"{PATH}{document_id}/update_version/",
                _TaskId,
                data=form,
                files={"document": (upload.filename, upload.content, upload.mime_type)},
            )
        except NotFound:
            raise unseen_document(document_id) from None
        return answer.root

    async def set_version_label(
        self, document_id: int, version_id: int, label: str | None
    ) -> DocumentVersion:
        """See ``DocumentWriter.set_version_label``.

        ``version_label`` null clears the label; Paperless also turns an empty string into null.
        It answers 404 to a missing document and to a version that is not on it, and 403 to a
        document the user may not see or change.
        """
        try:
            row = await self._client.call(
                "PATCH",
                f"{PATH}{document_id}/versions/{version_id}/",
                _Version,
                json={"version_label": label},
            )
        except NotFound:
            raise unseen_version(document_id, version_id) from None
        return _version(row)

    async def delete_version(self, document_id: int, version_id: int) -> int:
        """See ``DocumentWriter.delete_version``.

        Paperless answers 200 ``{"result": "OK", "current_version_id": ...}`` (its schema says
        204) and 400 with an HTML body for the root. Not retried, like ``delete_note``.
        """
        try:
            answer = await self._client.call(
                "DELETE",
                f"{PATH}{document_id}/versions/{version_id}/",
                _VersionDeleted,
                retry=False,
            )
        except NotFound:
            raise unseen_version(document_id, version_id) from None
        return answer.current_version_id

    async def merge_as_versions(
        self, root_id: int, source_ids: Sequence[int], label: str | None
    ) -> Unconfirmed | None:
        """See ``DocumentWriter.merge_as_versions``.

        The body lists the root first and names it again as ``root_document_id``. Paperless
        queues a task and names it in the answer (``task_id``), which is awaited; a user who may not
        change one of the documents gets 403 with an HTML body.
        """
        body: dict[str, object] = {"documents": [root_id, *source_ids], "root_document_id": root_id}
        if label is not None:
            body["version_label"] = label
        queued = await self._client.call("POST", MERGE_AS_VERSIONS_PATH, Queued, json=body)
        return await self._waiter.confirmed(queued, lambda _: not_applied(MERGE))
