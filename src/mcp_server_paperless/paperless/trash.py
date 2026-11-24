"""TrashReader and TrashWriter on ``/api/trash/``.

``GET`` lists the deleted documents the user may see, paginated, each as the full document row
plus ``deleted_at`` (the schema documents no body). A version deleted from a document is a row of
its own: its ``id`` is the version's, ``root_document`` the document's and ``title`` the
document's. A document in the trash covers its versions, which are then not listed. ``POST``
takes ``{action: restore|empty, documents: [ids]}`` and answers
``{"result": "OK", "doc_ids": [...]}`` once done. Probed on 3.4.2:

- An id that is not in the trash, or is listed twice, is a 400 for the whole request.
- A request without ``documents`` empties everything the user sees (``empty``) or is a 500
  (``restore``); an empty list does nothing. Both are why this adapter never sends fewer than one
  id.
- A restored version is re-attached to its document (with its label), and a restored document
  brings its versions back. A version whose document is also in the trash is a 400.
- The global ``delete_document`` permission is a 403 (JSON); a document the user may not delete is
  a 403 with the text/html body ``Insufficient permissions``.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, JsonValue

from mcp_server_paperless.domain.documents import CustomFieldValue, DocumentVersion
from mcp_server_paperless.domain.duplicates import DuplicateRecord
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.domain.trash import (
    TrashedDetail,
    TrashedDocument,
    TrashedNote,
    TrashedRecord,
    TrashLookup,
    TrashPage,
)
from mcp_server_paperless.paperless.pagination import Page

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from mcp_server_paperless.paperless.client import PaperlessClient

PATH = "/api/trash/"
# One request of many entries: Paperless looks up the versions of each row by itself.
SCAN_TIMEOUT: Final = 120.0


class _Version(BaseModel):
    checksum: str | None = None
    is_root: bool


class _Row(BaseModel):
    id: int
    title: str
    deleted_at: datetime
    root_document: int | None
    versions: list[_Version]
    created: date
    mime_type: str
    document_type: int | None
    owner: int | None
    archived_file_name: str | None


class _Facts(_Row):
    added: datetime
    modified: datetime
    archive_serial_number: int | None
    correspondent: int | None
    storage_path: int | None
    tags: list[int]
    original_file_name: str | None


class _User(BaseModel):
    username: str


class _Note(BaseModel):
    id: int
    note: str
    format: str | None = None
    created: datetime | None = None
    user: _User | None = None


class _Value(BaseModel):
    field: int
    value: JsonValue


class _Full(_Facts):
    """Every field of an entry. ``permissions`` is only sent on request (``full_perms``)."""

    versions: list[DocumentVersion]  # pyright: ignore[reportIncompatibleVariableOverride] -- read only
    created_date: date
    duplicate_documents: list[DuplicateRecord]
    user_can_change: bool
    is_shared_by_requester: bool
    notes: list[_Note]
    custom_fields: list[_Value]
    page_count: int | None
    content: str


class _Done(BaseModel):
    result: str


def beyond_last_page(page: int) -> NotFound:
    """The error for a page past the end of the trash."""
    return NotFound(
        f"Page {page} of the trash does not exist.",
        hint="List the trash with page=1; count and next_page tell how far it goes.",
    )


def refused() -> PermissionDenied:
    """The error for a 403 of ``POST``: the permission or a document's ownership is missing."""
    return PermissionDenied(
        "The Paperless user may not restore or empty these entries.",
        hint=(
            "It needs the permission to delete documents and must own every document (or it "
            "must have no owner)."
        ),
    )


def _checksum(row: _Row) -> str | None:
    # The row of a version lists its document's versions, not itself.
    roots = [v.checksum for v in row.versions if v.is_root and row.root_document is None]
    return next(iter(roots), None)


def _entry(row: _Row) -> TrashedDocument:
    return TrashedDocument(
        id=row.id,
        title=row.title,
        deleted_at=row.deleted_at,
        version_of=row.root_document,
        created=row.created,
        mime_type=row.mime_type,
        document_type=row.document_type,
        owner=row.owner,
        has_archive=row.archived_file_name is not None,
        checksum=_checksum(row),
    )


def _detail(row: _Full) -> TrashedDetail:
    notes = [
        TrashedNote(
            id=n.id,
            note=n.note,
            format=n.format,
            created=n.created,
            user=n.user.username if n.user else None,
        )
        for n in row.notes
    ]
    fields = [CustomFieldValue(field=v.field, value=v.value) for v in row.custom_fields]
    return TrashedDetail.model_validate(
        {
            **row.model_dump(exclude={"notes", "custom_fields", "root_document"}),
            "version_of": row.root_document,
            "notes": notes,
            "notes_count": len(notes),
            "custom_fields": fields,
        }
    )


class PaperlessTrashReader:
    """``TrashReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def list_trash(self, *, page: int, page_size: int) -> TrashPage:
        """See ``TrashReader.list_trash``."""
        try:
            found = await self._client.get(
                PATH, Page[_Row], params={"page": page, "page_size": page_size}
            )
        except NotFound:
            raise beyond_last_page(page) from None
        return TrashPage(
            count=found.count,
            next_page=found.next_page,
            entries=tuple(_entry(row) for row in found.results),
        )

    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        """See ``TrashReader.read_trash``; parsing a large answer stops the event loop for a while.

        Of the rows only what ``_Row`` names is kept.
        """
        found = await self._bounded(Page[_Row], entries, max_bytes)
        return TrashPage(
            count=found.count,
            next_page=found.next_page,
            entries=tuple(_entry(row) for row in found.results),
        )

    async def find_in_trash(
        self, document_ids: Collection[int], *, entries: int, max_bytes: int
    ) -> dict[int, TrashedRecord]:
        """See ``TrashReader.find_in_trash``."""
        found = await self._bounded(Page[_Facts], entries, max_bytes)
        return {
            row.id: TrashedRecord.model_validate({**row.model_dump(), "checksum": _checksum(row)})
            for row in found.results
            if row.root_document is None and row.id in document_ids
        }

    async def look_up_in_trash(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> TrashLookup:
        """See ``TrashReader.look_up_in_trash``."""
        try:
            found = await self._bounded(Page[_Full], page_size, max_bytes, page=page)
        except NotFound:
            raise beyond_last_page(page) from None
        row = next((row for row in found.results if row.id == document_id), None)
        return TrashLookup(entry=None if row is None else _detail(row), next_page=found.next_page)

    async def _bounded[M: BaseModel](
        self, model: type[M], entries: int, max_bytes: int, *, page: int = 1
    ) -> M:
        params = {"page": page, "page_size": entries}
        return await self._client.read_json(
            PATH, model, params=params, max_bytes=max_bytes, timeout=SCAN_TIMEOUT
        )


class PaperlessTrashWriter:
    """``TrashWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def restore_from_trash(self, document_ids: Sequence[int]) -> None:
        """See ``TrashWriter.restore_from_trash``."""
        await self._post("restore", document_ids)

    async def empty_trash(self, document_ids: Sequence[int]) -> None:
        """See ``TrashWriter.empty_trash``."""
        await self._post("empty", document_ids)

    async def _post(self, action: str, document_ids: Sequence[int]) -> None:
        if not document_ids:
            message = f"Name at least one trash entry to {action}."
            raise Validation(message, hint="Pass the ids from list_trash.")
        body = {"action": action, "documents": list(document_ids)}
        try:
            _ = await self._client.call("POST", PATH, _Done, json=body)
        except PermissionDenied:
            raise refused() from None
        except Validation as error:
            raise Validation(
                error.message, hint="Check the ids with list_trash; each must be in the trash."
            ) from None
