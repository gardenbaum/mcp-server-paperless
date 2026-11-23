"""Uploading a file as a new version of a document, with the checks of ``upload_document``.

Everything that can be checked is checked before the first byte is sent, because an upload cannot
be taken back: the file name, the file's type (from its bytes), its size, the label and the
document. The file, the size limit and the path allowlist are the same as for new documents
(``UploadService``). The new version becomes the newest one, which is the file and text the
document shows; the old versions stay. Paperless answers with the id of a consumption task and
consumes later. Uploading the same content twice makes two versions, so a file
Paperless already holds is sent only when that is confirmed (``DuplicateFinder``).
"""

from typing import TYPE_CHECKING

from mcp_server_paperless.domain.errors import Validation, quoted
from mcp_server_paperless.domain.versions import VersionUpload, VersionUploadResult
from mcp_server_paperless.services.documents import document_link
from mcp_server_paperless.services.task_outcome import follow
from mcp_server_paperless.services.upload_source import clean_filename, decode_base64, read_allowed
from mcp_server_paperless.services.upload_types import sniff
from mcp_server_paperless.services.uploads import UNSUPPORTED
from mcp_server_paperless.services.versions import clean_label, require_change

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.services.duplicates import DuplicateFinder
    from mcp_server_paperless.services.tasks import TaskService

NO_VERSION = (
    "Paperless consumed the file but names no version for it (the new version may be hidden "
    "from this user)."
)


class VersionUploadService:
    """Uploads files as new versions of documents."""

    def __init__(  # noqa: PLR0913 -- the ports and settings the use case needs
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        tasks: TaskService,
        duplicates: DuplicateFinder,
        *,
        web_url: str,
        max_bytes: int,
        upload_dirs: Sequence[str] = (),
        path_hint: str = "",
    ) -> None:
        """Upload through ``writer``, check the document with ``reader``, wait with ``tasks``.

        ``max_bytes`` bounds the file; ``upload_dirs`` is the allowlist for ``from_path``.
        ``path_hint`` says, with refused content, how a path could be enabled.
        """
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._tasks: TaskService = tasks
        self._duplicates: DuplicateFinder = duplicates
        self._web_url: str = web_url
        self._max_bytes: int = max_bytes
        self._dirs: tuple[str, ...] = tuple(upload_dirs)
        self._path_hint: str = path_hint

    async def from_content(  # noqa: PLR0913 -- the document, the file and its options
        self,
        document_id: int,
        content_base64: str,
        filename: str,
        label: str | None,
        *,
        wait_seconds: float = 0,
        confirm: bool = False,
    ) -> VersionUploadResult:
        """Upload base64 ``content_base64`` under ``filename`` as a new version.

        Raises:
            ConfirmationRequired: Paperless already holds the file and ``confirm`` is not set.
            Validation: The content, its type, the name or the label is not acceptable.
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not change the document.
        """
        name = clean_filename(filename)
        data = decode_base64(content_base64, max_bytes=self._max_bytes, path_hint=self._path_hint)
        return await self._upload(document_id, data, name, label, wait_seconds, confirm=confirm)

    async def from_path(  # noqa: PLR0913 -- the document, the file and its options
        self,
        document_id: int,
        path: str,
        filename: str | None,
        label: str | None,
        *,
        wait_seconds: float = 0,
        confirm: bool = False,
    ) -> VersionUploadResult:
        """Upload the file at ``path``, which must lie in an allowlisted directory.

        The name is ``filename``, else the file's own. Raises like ``from_content``, and
        ``NotFound`` when the path is outside the allowlist or not a regular file.
        """
        found, data = read_allowed(path, self._dirs, max_bytes=self._max_bytes)
        name = clean_filename(found if filename is None else filename)
        return await self._upload(document_id, data, name, label, wait_seconds, confirm=confirm)

    async def _upload(  # noqa: PLR0913 -- the document, the file and its options
        self,
        document_id: int,
        data: bytes,
        name: str,
        label: str | None,
        wait_seconds: float,
        *,
        confirm: bool,
    ) -> VersionUploadResult:
        mime_type = sniff(data, name)
        if mime_type is None:
            message = "The file is not a type Paperless can consume."
            raise Validation(message, hint=f"Supported: {UNSUPPORTED}")
        text = clean_label(label)
        record = await self._reader.get_document(document_id)
        require_change(record)  # missing, hidden or not ours
        upload = VersionUpload(filename=name, mime_type=mime_type, content=data, label=text)
        target = f"document {document_id} {quoted(record.title)}"
        before = await self._duplicates.cleared(
            data,
            f"upload {quoted(name)} as a new version of {target}",
            confirm=confirm,
            own=record,
        )
        task_id = await self._writer.upload_document_version(document_id, upload)
        outcome = await follow(
            self._tasks, task_id, wait_seconds, nothing_named=NO_VERSION, duplicates=True
        )
        return VersionUploadResult(
            task_id=task_id,
            state=outcome.state,
            message=outcome.message,
            document_id=document_id,
            version_id=outcome.document_id,
            web_url=document_link(self._web_url, document_id),
            wait=outcome.wait,
            waited_seconds=outcome.waited_seconds,
            filename=name,
            mime_type=mime_type,
            size_bytes=len(data),
            duplicates=before.checked(outcome.duplicates),
        )
