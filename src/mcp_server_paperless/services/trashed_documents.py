"""Looking into an entry of the Paperless trash: its fields, text, thumbnail and file.

Paperless serves less of a document in the trash than of a live one (read in 3.5.1): its text is
only in the rows of the trash, the file and thumbnail endpoints answer its first version, and
metadata, notes and history are 404. Files of a trashed document answer anyone with a view grant;
here the entry has to be in the trash list of the user, which shows owners and superusers only.
"""

import contextlib
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mcp_server_paperless.domain.documents import FileDelivery, FileReport, Thumbnail
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    NotFound,
    PaperlessMcpError,
    UpstreamUnavailable,
)
from mcp_server_paperless.domain.files import FilePart, FilePartReport
from mcp_server_paperless.domain.trash import TrashEntryDetail, TrashText
from mcp_server_paperless.services.documents import TRASH_BYTES
from mcp_server_paperless.services.transfer import (
    APPEND,
    MAX_SECONDS,
    changed,
    check_part,
    read_all,
    time_limit,
)
from mcp_server_paperless.services.trash import trash_link
from mcp_server_paperless.services.trash_scan import PAGE, all_too_large, following, limited

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import FileKind, FileVersion
    from mcp_server_paperless.domain.ports import DocumentReader, PermissionReader, TrashReader
    from mcp_server_paperless.domain.trash import TrashedDetail, TrashGrants, TrashLookup
    from mcp_server_paperless.services.documents import DocumentService
    from mcp_server_paperless.services.trash_scan import TrashScan

DOCUMENT_LIMITS = (
    "Of a document in the trash Paperless shows the text, thumbnail and file of its first version "
    "only, and no notes, custom field values, later versions, history, or size and checksum of "
    "the files as metadata. To read those, restore it first: restore_from_trash brings it back "
    "whole, without confirm."
)
VERSION_LIMITS = (
    "Text, thumbnail and file are those of this version, and its title, type, owner and other "
    "fields are those of its upload. Access follows the document, so permissions and "
    "user_can_change are those of its document; is_shared_by_requester is not known. Its "
    "history and the size of its files are not shown in the trash: restore it first with "
    "restore_from_trash, which attaches it to its document again, without confirm."
)
NO_CHANGE = " Whether the user may change it could not be read just now."
NO_PERMISSIONS = " Who may view or change it could not be read just now."
RESTORE_FIRST = "Restore it first with restore_from_trash, then use the tools for documents."
NO_FILE = "Paperless does not serve this file while the document is in the trash."


@dataclass(frozen=True, slots=True)
class _Page:
    """A page of the trash as it was asked for."""

    number: int
    size: int


class TrashedDocuments:
    """Reads entries of the trash for the current user."""

    def __init__(  # noqa: PLR0913 -- the ports and what they need
        self,
        trash: TrashReader,
        reader: DocumentReader,
        access: PermissionReader,
        documents: DocumentService,
        scan: TrashScan,
        *,
        web_url: str,
        max_bytes: int,
    ) -> None:
        """Read rows through ``trash``, files through ``reader`` and grants through ``access``.

        Files over ``max_bytes`` bytes are reported, not attached. ``documents`` names what an
        entry refers to.
        """
        self._trash: TrashReader = trash
        self._reader: DocumentReader = reader
        self._access: PermissionReader = access
        self._documents: DocumentService = documents
        self._scan: TrashScan = scan
        self._web_url: str = web_url
        self._max_bytes: int = max_bytes

    async def entry(
        self, document_id: int, *, text: bool = False, offset: int = 0, max_chars: int
    ) -> TrashEntryDetail:
        """Everything Paperless tells of an entry; with ``text`` a slice of its text.

        Raises:
            NotFound: It is not in the trash, or not visible to the user.
            AnswerTooLarge: The trash around it is too large to read.
            AnswerCompressed: A proxy compresses answers: their size is not known, and not read.
            PermissionDenied: The user may not view documents.
        """
        found, where = await self._find(document_id)
        if found.version_of is None:
            permissions = await self._permissions(document_id, where)
            can_change, shared = found.user_can_change, found.is_shared_by_requester
        else:
            permissions, can_change = await self._of_document(found.version_of)
            shared = None
        overview = await self._documents.detail(found)
        whole = found.content
        end = offset + max_chars
        return TrashEntryDetail(
            **{  # pyright: ignore[reportAny] -- the shared fields of two models
                name: getattr(overview, name)
                for name in TrashEntryDetail.model_fields.keys() & type(overview).model_fields
                if name not in {"web_url", "notes", "versions"}
            },
            web_url=trash_link(self._web_url),
            deleted_at=found.deleted_at,
            version_of=found.version_of,
            created_date=found.created_date,
            archived_file_name=found.archived_file_name,
            is_shared_by_requester=shared,
            user_can_change=can_change,
            permissions=permissions,
            notes=found.notes,
            versions=found.versions,
            duplicate_documents=found.duplicate_documents,
            text=TrashText(
                offset=offset,
                content=whole[offset:end],
                total_chars=len(whole),
                next_offset=end if end < len(whole) else None,
            )
            if text
            else None,
            limits=(DOCUMENT_LIMITS if found.version_of is None else VERSION_LIMITS)
            + (NO_PERMISSIONS if permissions is None else "")
            + (NO_CHANGE if can_change is None else ""),
        )

    async def _find(self, document_id: int) -> tuple[TrashedDetail, _Page]:
        """The entry and where it was: in one request if it is among the first, else in pages."""
        page: int | None = 1
        try:
            found = await self._look_up(document_id, large := _Page(1, self._scan.cap))
        except (AnswerTooLarge, UpstreamUnavailable) as error:
            if limited(error):
                raise
        else:
            if found.entry is not None:
                return found.entry, large
            page = 1 if found.next_page is not None else None
        while page is not None:
            small = _Page(page, PAGE)
            try:
                found = await self._look_up(document_id, small)
            except AnswerTooLarge:
                raise all_too_large() from None
            if found.entry is not None:
                return found.entry, small
            page = following(page, found.next_page)
        raise await self._scan.unlisted([document_id])

    async def _look_up(self, document_id: int, where: _Page) -> TrashLookup:
        return await self._trash.look_up_in_trash(
            document_id, page=where.number, page_size=where.size, max_bytes=TRASH_BYTES
        )

    async def _permissions(self, document_id: int, where: _Page) -> TrashGrants | None:
        """Who may view and change an entry, with names; None if that could not be read."""
        try:
            found = await self._trash.get_trash_permissions(
                document_id, page=where.number, page_size=where.size, max_bytes=TRASH_BYTES
            )
            return None if found is None else await self._documents.grants(found)
        except PaperlessMcpError:
            return None

    async def _of_document(self, document_id: int) -> tuple[TrashGrants | None, bool | None]:
        """Who may use the document of a version, and whether the user may change it.

        A version has grants of its own, but nothing looks at them. None for what could not be read.
        """
        grants, can_change = None, None
        with contextlib.suppress(PaperlessMcpError):
            for access in await self._access.get_permissions("document", [document_id]):
                grants = await self._documents.grants(access.permissions)
        with contextlib.suppress(PaperlessMcpError):
            can_change = (await self._reader.get_document(document_id)).user_can_change
        return grants, can_change

    async def thumbnail(self, document_id: int) -> Thumbnail:
        """The thumbnail of an entry.

        Raises:
            NotFound: It is not in the trash, or Paperless has no thumbnail of it.
            PermissionDenied: The user may not view documents.
        """
        _ = await self._scan.require([document_id])
        try:
            found = await self._reader.get_document_thumbnail(document_id)
        except NotFound:
            raise NotFound(NO_FILE, hint=RESTORE_FIRST) from None
        if not found.content:
            message = f"Paperless sent an empty thumbnail for trash entry {document_id}."
            raise PaperlessMcpError(message, hint=RESTORE_FIRST)
        report = self._report(document_id, "thumbnail", found.filename, found.mime_type)
        size = {"size_bytes": len(found.content), "delivered": True, "max_bytes": None}
        return Thumbnail(report=report.model_copy(update=size), data=found.content)

    async def download(self, document_id: int, file: FileVersion) -> FileDelivery:
        """The original or archive file of an entry, if it is within the limit.

        Raises:
            NotFound: It is not in the trash, has no archive file, or its file is not served.
            PermissionDenied: The user may not view documents.
        """
        await self._check_file(document_id, file)
        try:
            found = await self._reader.get_document_file(
                document_id, file, max_bytes=self._max_bytes
            )
        except NotFound:
            raise NotFound(NO_FILE, hint=RESTORE_FIRST) from None
        report = self._report(document_id, file, found.filename, found.mime_type)
        if found.truncated:
            note = (
                f"The file is over the download limit of {self._max_bytes} bytes, so it is not "
                "attached. Call again with offset=0 to read it in parts, read its text with "
                "get_trash_entry, or restore it with restore_from_trash."
            )
            return FileDelivery(report=report.model_copy(update={"note": note}), data=None)
        size = {"size_bytes": len(found.content), "delivered": True}
        return FileDelivery(report=report.model_copy(update=size), data=found.content)

    async def part(
        self,
        document_id: int,
        file: FileVersion,
        *,
        offset: int,
        max_bytes: int | None = None,
        checksum: str | None = None,
    ) -> FilePart[FilePartReport]:
        """The bytes of a file from ``offset``, at most ``max_bytes`` and the download limit.

        Paperless tells no checksum of a file in the trash, so it is read and hashed to its end
        each time: only that proves that the parts are of the same file (decision-45).

        Raises:
            NotFound: As ``download``.
            Validation: A part after the first does not say which file it belongs to, or that
                file changed.
            PaperlessMcpError: The file is too large.
        """
        check_part(offset, max_bytes, checksum)
        await self._check_file(document_id, file)
        limit = min(max_bytes or self._max_bytes, self._max_bytes)
        try:
            async with (
                time_limit(MAX_SECONDS),
                self._reader.open_document_file(document_id, file) as opened,
            ):
                found = await read_all(opened.chunks, offset, limit)
        except NotFound:
            raise NotFound(NO_FILE, hint=RESTORE_FIRST) from None
        data, size, known = found.window, found.size, found.sha256
        if checksum not in {None, known}:
            raise changed()
        end = offset + len(data)
        following = end if end < size else None
        arguments = (
            f"document_id={document_id}, file={file}, offset={end}, max_bytes={limit} and "
            f"checksum={known}"
        )
        report = FilePartReport(
            document_id=document_id,
            kind=file,
            version_id=document_id,
            filename=opened.filename,
            mime_type=opened.mime_type,
            offset=offset,
            length=len(data),
            total_bytes=size,
            next_offset=following,
            part_sha256=hashlib.sha256(data).hexdigest(),
            checksum=known,
            web_url=trash_link(self._web_url),
            next=None if following is None else f"Call again with {arguments}; {APPEND}",
        )
        return FilePart(report=report, data=data)

    async def _check_file(self, document_id: int, file: FileVersion) -> None:
        """Refuse what is no entry, or has no such file: Paperless would send the original."""
        [entry], _ = await self._scan.require([document_id])
        if file == "archive" and not entry.has_archive:
            message = f"Trash entry {document_id} has no archive version."
            raise NotFound(message, hint='Ask for file="original"; it is the only file it has.')

    def _report(
        self, document_id: int, file: FileKind, filename: str | None, mime_type: str
    ) -> FileReport:
        return FileReport(
            document_id=document_id,
            kind=file,
            filename=filename,
            mime_type=mime_type,
            size_bytes=None,
            delivered=False,
            max_bytes=self._max_bytes,
            web_url=trash_link(self._web_url),
            note=None,
            version_id=document_id,
        )
