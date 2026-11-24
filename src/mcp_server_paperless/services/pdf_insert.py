"""Inserting pages of one document into another: built here, uploaded once (decision-68).

Paperless cannot insert pages. Both files are read, the renderer's child process puts them together,
and the result is uploaded as one new version or one new document. Until that one request nothing
is written, so a step that fails leaves nothing behind, and both documents are read again just
before it.
"""

import asyncio
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import DocumentUpload
from mcp_server_paperless.domain.errors import (
    PaperlessMcpError,
    UnexpectedAnswer,
    UpstreamUnavailable,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.files import NOUNS, STILL_READ, missing_file
from mcp_server_paperless.domain.pdf import PdfPlan, PdfResult, PdfTask
from mcp_server_paperless.domain.rendering import MAX_PAGES, AssembleRequest, RenderFailed
from mcp_server_paperless.domain.versions import VersionUpload
from mcp_server_paperless.services.allowed_paths import clean_filename
from mcp_server_paperless.services.documents import document_link, listed_version
from mcp_server_paperless.services.page_ranges import parse_pages, specification
from mcp_server_paperless.services.pages import (
    DOWNLOAD_SECONDS,
    MAX_SOURCE_BYTES,
    PDF,
    RENDER_MEMORY,
    RENDER_SECONDS,
    WAIT_SECONDS,
    named_size,
    taking,
)
from mcp_server_paperless.services.pdf import NO_PAGES_HINT
from mcp_server_paperless.services.pdf_plans import (
    NEVER_COPIED,
    NEW_DOCUMENT,
    UNSIGNED,
    numbers,
    version_plan,
)
from mcp_server_paperless.services.pdf_replacing import FIND
from mcp_server_paperless.services.task_outcome import follow
from mcp_server_paperless.services.tasks import MAX_WAIT_SECONDS
from mcp_server_paperless.services.uploads import NO_DOCUMENT
from mcp_server_paperless.services.version_uploads import NO_VERSION
from mcp_server_paperless.services.versions import require_change

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import DocumentRecord, FileMetadata, FileVersion
    from mcp_server_paperless.domain.pdf import FileOutput
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.domain.rendering import Assembled, PageRenderer, Reason
    from mcp_server_paperless.services.task_outcome import Outcome
    from mcp_server_paperless.services.tasks import TaskService

# The one status of an unavailable Paperless that says the request was not taken.
TOO_MANY: Final = 429
NOTHING: Final = "Nothing was written."
FEWER: Final = "Insert fewer pages."
UNLOCK: Final = "remove_document_password (toolset pdf) makes a version without the password."
REWRITE: Final = (
    "It opens without a password but restricts what may be done with it. edit_pdf with all its "
    'pages and output="new_version" makes a version without that: it changes that document and '
    "needs ownership of it."
)
STRUCTURE: Final = (
    "bookmarks, attachments, form fields, annotations and properties of the file of document {id}"
)
SOURCE_LOST: Final = (
    "bookmarks, document attachments and scripts, properties, layer settings (a hidden layer can "
    "become visible) and the form of the inserted pages are not in the result: their fields show "
    "their values but cannot be filled in, characters their font lacks are missing, and "
    "get_document_pages and inspect_pdf draw them only in a file that has a form"
)
ALONG: Final = (
    "what the inserted pages refer to, also where it is not visible: links, comments with the "
    "replies to them and the files in them, actions, private and page data, and resources they "
    "share with other pages of the source"
)
UNCHECKED: Final = "accessibility tags and PDF/A conformance of the result are not checked"
UNGRANTED: Final = "view and change grants are not copied"
COPIED: Final = (
    "the new document takes title, correspondent, document type, storage path, tags, created date "
    "and custom field values of {name}"
)
READABLE: Final = ", which everybody can read who may see the document"


@dataclass(frozen=True, slots=True)
class _File:
    """One of the two files.

    Attributes:
        wanted: The version the caller chose, None for the newest.
    """

    document: DocumentRecord
    version: int
    kind: FileVersion
    facts: FileMetadata
    wanted: int | None = None

    @property
    def name(self) -> str:
        """The document in words."""
        return f"document {self.document.id} {quoted(self.document.title)}"

    @property
    def noun(self) -> str:
        """The file in words."""
        return f"The {NOUNS[self.kind]} of document {self.document.id}"

    @property
    def size(self) -> int:
        """Its bytes, as far as Paperless says."""
        facts = self.facts
        return (facts.archive_size if self.kind == "archive" else facts.original_size) or 0


def _as_pdf(document: DocumentRecord, version: int, facts: FileMetadata) -> FileVersion:
    """The PDF to take pages from: the original, or the archive version of another type.

    Raises:
        Validation: There is none.
        NotFound: Paperless cannot find it.
    """
    kind: FileVersion = "original" if facts.original_mime_type == PDF else "archive"
    if kind == "archive" and not facts.has_archive_version:
        message = (
            f"Document {document.id} is not a PDF ({facts.original_mime_type}) and has no archive "
            "PDF to take pages from."
        )
        raise Validation(message, hint="Take pages from a PDF, or from a document with an archive.")
    if facts.lacks(kind):
        raise missing_file(document.id, kind, facts, version, hint=STILL_READ)
    return kind


class PageInserter:
    """Inserts pages for the current user."""

    def __init__(  # noqa: PLR0913 -- the parts and the limits are injected
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        renderer: PageRenderer,
        tasks: TaskService,
        turn: asyncio.Semaphore,
        *,
        web_url: str,
        max_bytes: int = MAX_SOURCE_BYTES,
        wait_seconds: float = WAIT_SECONDS,
        download_seconds: float = DOWNLOAD_SECONDS,
    ) -> None:
        """Read through ``reader``, build with ``renderer`` in its ``turn``, send with ``writer``.

        ``max_bytes`` bounds both files together and the result.
        """
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._renderer: PageRenderer = renderer
        self._tasks: TaskService = tasks
        self._turn: asyncio.Semaphore = turn
        self._web_url: str = web_url
        self._max_bytes: int = max_bytes
        self._wait_seconds: float = wait_seconds
        self._download_seconds: float = download_seconds

    async def insert(  # noqa: PLR0913 -- the two documents, the place and the options
        self,
        document_id: int,
        source_document_id: int,
        pages: str,
        after_page: int,
        *,
        source_version_id: int | None = None,
        output: FileOutput = "new_version",
        dry_run: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Insert ``pages`` of the source after a page of the document; 0 puts them in front.

        The document gets a new version, or a new document takes its metadata. A dry run does
        everything but the upload.

        Raises:
            NotFound: A document, its version or its file does not exist or cannot be seen.
            PermissionDenied: The user may not change the document, or add one.
            Validation: The pages, the place or a file cannot be used.
            PaperlessMcpError: The files cannot be put together within the limits, a document
                changed meanwhile, or it is not known whether Paperless took the result.
        """
        version = output == "new_version"
        target, count = await self._target(document_id, after_page, version=version)
        source = await self._source(source_document_id, source_version_id, pages)
        if target.document.id == source.document.id:
            message = "A document cannot be its own source."
            raise Validation(message, hint="edit_pdf can repeat and reorder its pages.")
        request = AssembleRequest(
            pages=pages, after_page=after_page, target_pages=count, max_bytes=self._max_bytes
        )
        made = await self._made(target, source, request)
        place = f"after page {after_page} of" if after_page else "in front of"
        noun = "page" if len(made.inserted) == 1 else "pages"
        summary = (
            f"insert {noun} {specification(made.inserted)} of {source.name} {place} {target.name}"
        )
        sources = (document_id, source_document_id)
        links = tuple(document_link(self._web_url, number) for number in sources)
        result = PdfResult(
            operation="insert_pages",
            summary=summary if version else f"{summary}, as a new document",
            dry_run=True,
            plan=_plan(target, source, count + len(made.inserted), version=version),
            source_document_ids=sources,
            state="not_started",
            message=None,
            source_links=links,
            tasks=(),
            produced_ids=(),
            produced_links=(),
            trashed_ids=(),
            next=None,
            wait="not_requested",
            waited_seconds=0,
        )
        if dry_run:
            return result
        task_id = await self._sent(target, source, made.content, version=version)
        del made  # up to 100 MiB, while other calls may build theirs
        outcome = await follow(
            self._tasks,
            task_id,
            min(max(wait_seconds, 0), MAX_WAIT_SECONDS),
            nothing_named=NO_VERSION if version else NO_DOCUMENT,
        )
        return self._reported(result, task_id, outcome, version=version)

    async def _target(
        self, document_id: int, after_page: int, *, version: bool
    ) -> tuple[_File, int]:
        """The file pages go into, and how many it has."""
        document = await self._reader.get_document(document_id)
        if version:
            require_change(document)
        shown = listed_version(document, None)
        facts = await self._reader.get_document_metadata(document_id, version_id=shown)
        if facts.original_mime_type != PDF:
            message = f"Document {document_id} is not a PDF ({facts.original_mime_type})."
            hint = (
                "Pages go into PDF files only. merge_documents with archive_fallback makes a new "
                "document with the archive PDF of other files."
            )
            raise Validation(message, hint=hint)
        if facts.lacks("original"):
            raise missing_file(document_id, "original", facts, shown, hint=STILL_READ)
        count = document.page_count
        if count is None:
            message = f"The page count of document {document_id} is unknown."
            raise Validation(message, hint=NO_PAGES_HINT)
        if not 0 <= after_page <= count:
            pages = "1 page" if count == 1 else f"{count} pages"
            message = f"Page {after_page} does not exist: document {document_id} has {pages}."
            raise Validation(message, hint=f"Use 0 (in front) to {count} (at the end).")
        return _File(document, shown, "original", facts), count

    async def _source(self, document_id: int, version_id: int | None, pages: str) -> _File:
        """The file the pages come from; they are checked if Paperless counted them."""
        document = await self._reader.get_document(document_id)
        shown = listed_version(document, version_id)
        facts = await self._reader.get_document_metadata(document_id, version_id=shown)
        kind = _as_pdf(document, shown, facts)
        if kind == "original":
            count = document.page_count
            if shown != listed_version(document, None):
                count = await self._reader.get_version_pages(document_id, shown)
            if count is not None:
                _ = parse_pages(pages, page_count=count)
        return _File(document, shown, kind, facts, version_id)

    async def _made(self, target: _File, source: _File, request: AssembleRequest) -> Assembled:
        """The new file, checked by the renderer."""
        if target.size + source.size > self._max_bytes:
            raise self._too_large(target, source)
        async with taking(self._turn, self._wait_seconds):
            try:
                async with asyncio.timeout(self._download_seconds):
                    contents: list[bytes] = []
                    for file in (target, source):
                        sent = await self._reader.get_document_file(
                            file.document.id,
                            file.kind,
                            max_bytes=self._max_bytes - sum(map(len, contents)),
                            version_id=file.version,
                        )
                        if sent.truncated:
                            # Larger than its metadata says, or of unknown size.
                            raise self._too_large(target, source)
                        contents.append(sent.content)
            except TimeoutError:
                message = (
                    f"Paperless did not send the files of documents {target.document.id} and "
                    f"{source.document.id} within {self._download_seconds:.0f} seconds. {NOTHING}"
                )
                raise PaperlessMcpError(message, hint="Try again later.") from None
            try:
                return await self._renderer.assemble(contents[0], contents[1], request)
            except RenderFailed as failure:
                if failure.reason == "pages":
                    # Raises the error of the specification, in this process's words.
                    _ = parse_pages(request.pages, page_count=failure.page_count)
                raise self._failed(failure, target, source, request.target_pages) from failure

    def _too_large(self, target: _File, source: _File) -> PaperlessMcpError:
        message = (
            f"The files of documents {target.document.id} and {source.document.id} are together "
            f"over {named_size(self._max_bytes)}, the most that is put together. {NOTHING}"
        )
        return PaperlessMcpError(message)

    def _failed(
        self, failure: RenderFailed, target: _File, source: _File, count: int
    ) -> PaperlessMcpError:
        """Why there is no new file, and what to do."""
        noun = source.noun if failure.of_source else target.noun
        counted = "1 page" if count == 1 else f"{count} pages"
        many = f"{noun} has" if failure.of_source else "The result would have"
        reasons: dict[Reason, tuple[str, str | None]] = {
            "encrypted": (f"{noun} is encrypted.", UNLOCK),
            "restricted": (f"{noun} is encrypted.", REWRITE),
            "damaged": (f"{noun} is damaged and cannot be put together.", None),
            "differs": (
                f"{noun} does not have the {counted} that Paperless counted: it is damaged.",
                None,
            ),
            "count": (f"{many} more than {MAX_PAGES} pages.", None),
            "large": (f"The result would be over {named_size(self._max_bytes)}.", FEWER),
            "timeout": (
                (
                    f"Putting the files together took longer than {RENDER_SECONDS} seconds and was "
                    "stopped."
                ),
                FEWER,
            ),
            "memory": (
                (
                    f"Putting the files together needed more than {RENDER_MEMORY} of memory and "
                    "was stopped."
                ),
                FEWER,
            ),
        }
        unknown = ("The files could not be put together.", None)
        message, hint = reasons.get(failure.reason, unknown)
        return PaperlessMcpError(f"{message} {NOTHING}", hint=hint)

    async def _sent(self, target: _File, source: _File, content: bytes, *, version: bool) -> str:
        """The id of the task of the one upload."""
        current = await self._same(target, version=version)
        _ = await self._same(source)
        number, name = target.document.id, _filename(target)
        try:
            if version:
                file = VersionUpload(filename=name, mime_type=PDF, content=content)
                return await self._writer.upload_document_version(number, file)
            return await self._writer.upload_document(
                DocumentUpload(
                    filename=name,
                    mime_type=PDF,
                    content=content,
                    title=current.title,
                    created=current.created,
                    correspondent=current.correspondent,
                    document_type=current.document_type,
                    storage_path=current.storage_path,
                    tags=current.tags,
                    custom_fields=current.custom_fields,
                )
            )
        except (UpstreamUnavailable, UnexpectedAnswer) as error:
            if isinstance(error, UpstreamUnavailable) and error.status == TOO_MANY:
                raise
            what = "the new version" if version else "the new document"
            message = f"It is not known whether Paperless took {what}: {error.message}"
            where = (
                f"Look at the versions of document {number} (get_document with include versions)"
                if version
                else "Look for it with search_documents (newest added first)"
            )
            raise PaperlessMcpError(message, hint=f"{where} before you repeat the call.") from error

    async def _same(self, file: _File, *, version: bool = False) -> DocumentRecord:
        """The document as it is now, which must still have the file that was read."""
        document = await self._reader.get_document(file.document.id)
        if version:
            require_change(document)
        if listed_version(document, file.wanted) != file.version:
            message = f"Document {document.id} got another file meanwhile. {NOTHING}"
            raise PaperlessMcpError(message, hint="Look at it and call the tool again.")
        return document

    def _reported(
        self, result: PdfResult, task_id: str, outcome: Outcome, *, version: bool
    ) -> PdfResult:
        """``result`` with what became of the upload."""
        target = result.source_document_ids[0]
        produced, message, following = outcome.document_ids, outcome.message, None
        if outcome.state in {"pending", "started"}:
            following = f'get_task(task_id="{task_id}", wait_seconds={MAX_WAIT_SECONDS})'
        elif outcome.state == "unknown" or (outcome.state == "success" and not produced):
            found = f'get_document(document_id={target}, include=["versions"])'
            following = found if version else FIND
        elif outcome.state != "success" and version:
            shows = f"Document {target} shows the file it showed before."
            message = f"{message} {shows}" if message else shows
        task = PdfTask(
            task_id=task_id, state=outcome.state, message=outcome.message, document_ids=produced
        )
        linked = (target,) * len(produced) if version else produced
        changed = {
            "dry_run": False,
            "state": outcome.state,
            "message": message,
            "tasks": (task,),
            "produced_ids": produced,
            "produced_links": tuple(document_link(self._web_url, n) for n in linked),
            "next": following,
            "wait": outcome.wait,
            "waited_seconds": outcome.waited_seconds,
        }
        return result.model_copy(update=changed)


def _filename(target: _File) -> str:
    """The name of the target's file, as a PDF; a plain one if it cannot be sent."""
    stem = PurePath((target.facts.original_filename or "").replace("\\", "/")).stem
    try:
        return clean_filename(f"{stem}.pdf" if stem else "")
    except Validation:
        return f"document-{target.document.id}.pdf"


def _plan(target: _File, source: _File, pages: int, *, version: bool) -> PdfPlan:
    """What is made, kept and lost."""
    size = f"with {pages} pages"
    if source.kind == "archive":
        mime_type = source.facts.original_mime_type
        size += f", from the archive PDF of document {source.document.id} (its file is {mime_type})"
    structure = STRUCTURE.format(id=target.document.id)
    untouched = f"{source.name}: unchanged"
    loses = (UNSIGNED, SOURCE_LOST, UNCHECKED)
    if version:
        produces = f"a new version of the document {size}{READABLE}"
        plan = version_plan(produces, target.name, loses=loses)
        return plan.model_copy(update={"keeps": (*plan.keeps, structure, ALONG, untouched)})
    serials = numbers([target.document], None, trashes=False)
    return PdfPlan(
        produces=f"1 new document {size}",
        keeps=(
            COPIED.format(name=target.name),
            structure,
            f"{target.name}: unchanged",
            ALONG,
            untouched,
            *serials.keeps,
        ),
        loses=(*loses, UNGRANTED, NEVER_COPIED, NEW_DOCUMENT),
        needs_confirm=False,
    )
