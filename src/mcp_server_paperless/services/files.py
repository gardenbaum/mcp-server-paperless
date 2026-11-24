"""Thumbnails and downloads of documents, with the size limit applied before the bytes are read.

Callers get a report for every file and the bytes only when the file is within the limit. A larger
one is read in parts, each within the limit, or saved into a directory the operator allowed (over
stdio only); nothing else is ever written to the host (decision-7, decision-45).
"""

import hashlib
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING

from mcp_server_paperless.domain.documents import FileDelivery, FileReport, Thumbnail
from mcp_server_paperless.domain.errors import NotFound, PaperlessMcpError, Validation
from mcp_server_paperless.domain.files import STILL_READ, FilePart, FilePartReport, SavedFileReport
from mcp_server_paperless.services.documents import document_link, listed_version
from mcp_server_paperless.services.file_sink import NO_SINK, Expected, file_name
from mcp_server_paperless.services.transfer import (
    APPEND,
    MAX_FILE_BYTES,
    MAX_SECONDS,
    REPEAT,
    changed,
    check_part,
    incomplete,
    read_window,
    time_limit,
    too_large,
    ways,
)

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import FileMetadata, FileVersion
    from mcp_server_paperless.domain.ports import DocumentReader
    from mcp_server_paperless.services.file_sink import FileSink

PDF = "application/pdf"


def _over_limit(limit: int, link: str, *, saving: bool) -> str:
    return (
        f"The file is over the download limit of {limit} bytes, so it is not attached. "
        f"Call again with {ways(saving=saving)}, read its text with get_document_content, "
        f"open it at {link}, or ask the administrator to raise PAPERLESS_MCP_MAX_DOWNLOAD_BYTES."
    )


@dataclass(frozen=True, slots=True)
class _Facts:
    """What Paperless says about the file that was chosen.

    Attributes:
        name: The name of the upload (the archive version with .pdf), or None.
    """

    size: int | None
    checksum: str | None
    mime_type: str
    name: str | None


def _facts(document_id: int, metadata: FileMetadata, version: FileVersion) -> _Facts:
    """The facts of the original or of the archive version.

    Raises:
        NotFound: The document has no archive version.
    """
    name = PurePath(metadata.original_filename or "").name or None
    if version == "original":
        return _Facts(
            metadata.original_size, metadata.original_checksum, metadata.original_mime_type, name
        )
    if not metadata.has_archive_version:
        message = f"Document {document_id} has no archive version."
        hint = 'Call download_document with kind="original"; it is the only file the document has.'
        if metadata.lacks("original"):
            hint = STILL_READ
        raise NotFound(message, hint=hint)
    pdf = PurePath(name).with_suffix(".pdf").name if name else None
    return _Facts(metadata.archive_size, metadata.archive_checksum, PDF, pdf)


class FileService:
    """Reads the files of the documents the current user may see."""

    def __init__(
        self,
        reader: DocumentReader,
        *,
        web_url: str,
        max_bytes: int,
        sink: FileSink = NO_SINK,
    ) -> None:
        """Read through ``reader``; files over ``max_bytes`` bytes are reported, not delivered.

        ``web_url`` is the base of links to the Paperless UI. ``sink`` saves files; by default it
        refuses to.
        """
        self._sink: FileSink = sink
        self._reader: DocumentReader = reader
        self._web_url: str = web_url
        self._max_bytes: int = max_bytes

    async def thumbnail(self, document_id: int) -> Thumbnail:
        """The thumbnail image of a document; thumbnails are small and have no limit.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not see the document.
            PaperlessMcpError: Paperless sent an empty image.
        """
        found = await self._reader.get_document_thumbnail(document_id)
        if not found.content:
            message = f"Paperless sent an empty thumbnail for document {document_id}."
            raise PaperlessMcpError(message)
        report = FileReport(
            document_id=document_id,
            kind="thumbnail",
            filename=found.filename,
            mime_type=found.mime_type,
            size_bytes=len(found.content),
            delivered=True,
            max_bytes=None,
            web_url=document_link(self._web_url, document_id),
            note=None,
        )
        return Thumbnail(report=report, data=found.content)

    async def download(
        self, document_id: int, version: FileVersion, *, version_id: int | None = None
    ) -> FileDelivery:
        """The original or archive version of a document, if it is within the limit.

        ``version_id`` names one of the versions the document lists (default: the newest). A size
        the file metadata gives decides before anything is downloaded; otherwise (an archive
        version of unknown size, or metadata that understates it) the download itself stops at the
        limit, so no more than the limit is ever read.

        Raises:
            NotFound: No such document or version, or ``archive`` asked of a document without one.
            PermissionDenied: The user may not see the document.
        """
        if version_id is not None:
            _ = listed_version(await self._reader.get_document(document_id), version_id)
        metadata = await self._reader.get_document_metadata(document_id, version_id=version_id)
        facts = _facts(document_id, metadata, version)
        if facts.size is not None and facts.size > self._max_bytes:
            filename, mime_type, size, data = facts.name, facts.mime_type, facts.size, None
        else:
            found = await self._reader.get_document_file(
                document_id, version, max_bytes=self._max_bytes, version_id=version_id
            )
            data = None if found.truncated else found.content
            filename, mime_type = found.filename, found.mime_type
            size = None if data is None else len(data)
        link = document_link(self._web_url, document_id)
        report = FileReport(
            document_id=document_id,
            kind=version,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size,
            delivered=data is not None,
            max_bytes=self._max_bytes,
            web_url=link,
            note=None
            if data is not None
            else _over_limit(self._max_bytes, link, saving=bool(self._sink.roots)),
            version_id=version_id,
        )
        return FileDelivery(report=report, data=data)

    async def part(  # noqa: PLR0913 -- the file, the window and the proof
        self,
        document_id: int,
        version: FileVersion,
        *,
        offset: int,
        version_id: int | None = None,
        max_bytes: int | None = None,
        checksum: str | None = None,
    ) -> FilePart[FilePartReport]:
        """The bytes of a file from ``offset``, at most ``max_bytes`` and the download limit.

        Raises:
            NotFound: No such document, version or archive version.
            PermissionDenied: The user may not see the document.
            Validation: A part after the first does not say which file it belongs to, or that
                file changed.
            PaperlessMcpError: The file is too large, or Paperless does not describe it.
        """
        check_part(offset, max_bytes, checksum)
        if offset and version_id is None:
            message = "A part after the first needs the version_id of the first part's report."
            raise Validation(message, hint=REPEAT)
        shown = listed_version(await self._reader.get_document(document_id), version_id)
        metadata = await self._reader.get_document_metadata(document_id, version_id=shown)
        facts = _facts(document_id, metadata, version)
        link = document_link(self._web_url, document_id)
        size, known = facts.size, facts.checksum
        if size is None:
            # The file is missing: this says so.
            async with (
                time_limit(MAX_SECONDS),
                self._reader.open_document_file(document_id, version, version_id=shown),
            ):
                pass
        if size is None or known is None:
            noun = "archive" if version == "archive" else "original"
            message = (
                f"Paperless does not give the size and checksum of the {noun} of document "
                f"{document_id}, so it is not read in parts."
            )
            raise PaperlessMcpError(message, hint=f"Open it at {link}.")
        if checksum not in {None, known}:
            raise changed()
        if size > MAX_FILE_BYTES:
            raise too_large()
        limit = min(max_bytes or self._max_bytes, self._max_bytes)
        filename, mime_type, data = facts.name, facts.mime_type, b""
        if offset < size:
            async with (
                time_limit(MAX_SECONDS),
                self._reader.open_document_file(document_id, version, version_id=shown) as opened,
            ):
                data = await read_window(opened.chunks, offset, limit)
            filename, mime_type = opened.filename, opened.mime_type
            again = await self._reader.get_document_metadata(document_id, version_id=shown)
            if _facts(document_id, again, version).checksum != known:
                raise changed()
            if len(data) != min(limit, size - offset):
                raise incomplete()
        end = offset + len(data)
        following = end if end < size else None
        arguments = (
            f"document_id={document_id}, kind={version}, version_id={shown}, offset={end}, "
            f"max_bytes={limit} and checksum={known}"
        )
        report = FilePartReport(
            document_id=document_id,
            kind=version,
            version_id=shown,
            filename=filename,
            mime_type=mime_type,
            offset=offset,
            length=len(data),
            total_bytes=size,
            next_offset=following,
            part_sha256=hashlib.sha256(data).hexdigest(),
            checksum=known,
            web_url=link,
            next=None if following is None else f"Call again with {arguments}; {APPEND}",
        )
        return FilePart(report=report, data=data)

    async def save(  # noqa: PLR0913 -- the file and where it goes
        self,
        document_id: int,
        version: FileVersion,
        *,
        directory: str,
        version_id: int | None = None,
        save_as: str | None = None,
        confirm: bool = False,
    ) -> SavedFileReport:
        """Save a file of any size up to the limit into ``directory``.

        Raises:
            NotFound: No such document, version or archive version, or no usable directory.
            PermissionDenied: The user may not see the document.
            Validation: Saving is not enabled, or the name is not usable.
            ConfirmationRequired: A file of that name exists and ``confirm`` is not set.
            PaperlessMcpError: The file could not be read or written completely; nothing is saved.
        """
        async with self._sink.open(directory) as target:
            shown = listed_version(await self._reader.get_document(document_id), version_id)
            metadata = await self._reader.get_document_metadata(document_id, version_id=shown)
            facts = _facts(document_id, metadata, version)
            async with self._reader.open_document_file(
                document_id, version, version_id=shown
            ) as opened:
                proposed = opened.filename or facts.name or f"document-{document_id}"
                # The name is known only now; the bytes are not read yet.
                name = file_name(save_as, proposed, facts.mime_type)
                noun = "archive version" if version == "archive" else "original"
                await target.claim(
                    name,
                    confirm=confirm,
                    what=f"the {noun} of document {document_id} (version {shown})",
                    source=("document", document_id, version, shown, facts.checksum, facts.size),
                )
                saved = await target.write(opened.chunks, Expected(facts.size, facts.checksum))
        return SavedFileReport(
            document_id=document_id,
            kind=version,
            version_id=shown,
            directory=directory,
            filename=saved.filename,
            size_bytes=saved.size,
            sha256=saved.sha256,
            replaced=saved.replaced,
            web_url=document_link(self._web_url, document_id),
        )
