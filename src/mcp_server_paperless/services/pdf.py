"""PDF operations: rotate, merge, split, extract, edit, remove a password, reprocess.

Paperless runs these as background tasks and answers "OK" without naming them; the adapter finds
the tasks, and this service reports them like an upload does (``task_outcome``), with an optional
bounded wait. Everything Paperless would refuse with a 400 and this server can see beforehand is
refused first: page ranges against the page count, the file type, the numbering of output files.

What destroys something goes through the confirmation flow (decision-6, decision-15): deleting
originals (they move to the Paperless trash), a new version that drops pages from the file the
document shows or goes back to an older file (the earlier version stays, but the document no longer
shows it), and reprocessing (the archived file and the text are replaced). Making new documents, and
adding a version that keeps every page (rotating, unlocking), changes nothing that existed. Every
operation knows its plan before it runs, and ``dry_run`` returns it without starting anything.

The file that is read is always named by its version: Paperless holds the pages of a request against
the row it is sent, which for a document is its first file. Originals are trashed here, as the last
step (``pdf_replacing``), never by Paperless (decision-60).
"""

import contextlib
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import UploadState  # noqa: TC001 -- used at runtime in a constant
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.pdf import PageSlot, PdfPlan, PdfResult, PdfTask
from mcp_server_paperless.services.documents import document_link, listed_version, require_files
from mcp_server_paperless.services.page_ranges import describe, parse_pages, specification
from mcp_server_paperless.services.pdf_parts import part_lines, unset_reason
from mcp_server_paperless.services.pdf_plans import (
    REBUILT,
    UNSIGNED,
    document_plan,
    numbers,
    outputs,
    shortening,
    version_plan,
    without,
)
from mcp_server_paperless.services.pdf_replacing import (
    REFUSALS,
    KeptError,
    Replacement,
    cause,
    if_unchanged,
    noted,
    stays,
)
from mcp_server_paperless.services.removal import WAY_BACK
from mcp_server_paperless.services.task_outcome import follow
from mcp_server_paperless.services.tasks import MAX_WAIT_SECONDS
from mcp_server_paperless.services.versions import require_change

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord, FileMetadata
    from mcp_server_paperless.domain.pdf import FileOutput, Output, PartMetadata, StartedTasks
    from mcp_server_paperless.domain.ports import DocumentReader, PdfWriter
    from mcp_server_paperless.domain.tasks import Wait
    from mcp_server_paperless.services.pdf_parts import Found, Part, Parts
    from mcp_server_paperless.services.pdf_plans import Numbers
    from mcp_server_paperless.services.pdf_replacing import Replacing
    from mcp_server_paperless.services.tasks import TaskService
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# Most documents one call works on: each is read first, so the call stays small.
MAX_DOCUMENTS: Final = 20
# Most parts a split makes, and most pages an edit lists.
MAX_PARTS: Final = 20
MAX_PAGES: Final = 500
PDF: Final = "application/pdf"
TRASH: Final = "move the original to the Paperless trash"
FINAL: Final = frozenset({"success", "failure", "revoked"})
FORBIDDEN: Final = (
    "Paperless accepted the request, but the Paperless user may not view tasks, so none can be "
    "followed."
)
UNCONFIRMED: Final = (
    "Paperless accepted the request, but its queued task could not be followed to the end "
    "({detail}): the files it makes are not known yet. Follow the task with get_task."
)
UNREADABLE: Final = "Paperless accepted the request; its tasks could not be read."
NONE_FOUND: Final = "Paperless accepted the request, but no task showed up for it{reason}."
SOME_FOUND: Final = (
    "Paperless accepted the request, but only {found} of the {expected} tasks it starts showed "
    "up: the state covers {those} only{reason}."
)
UNLOCKED_NONE: Final = "the file may not be password protected"
DATED: Final = (
    "its custom fields hold a date, for which Paperless 3.5 lists no task: the work is done all "
    "the same"
)
BY_NAME: Final = (
    "Paperless lists no task for it, because a custom field holds a date: the documents were found "
    "by their file names."
)
NOT_TAKEN: Final = (
    "The new documents could not be told by their file names either ({doubt}): nothing is done to "
    "them."
)
EVERY_PART: Final = "the calls in next of every part, then "
NOT_REMOVED: Final = "No page was removed from document {id}: {{reason}}."
MAYBE_REMOVED: Final = (
    "It is not known whether the pages were removed from document {id} ({cause}): document "
    "{product} holds them."
)
VANISH: Final = (
    "and remove them from it: the document then shows a new version without them, and the earlier "
    "version with all pages stays until that version is deleted"
)
BACK: Final = WAY_BACK.rstrip(".")
ARCHIVE_ONLY: Final = (
    "{name}: only its archive PDF is in the merged document, its own file goes to the trash with it"
)
NO_PAGES_HINT: Final = (
    "It may be password protected (remove_document_password unlocks it) or damaged."
)
UNREAD_RIGHTS: Final = (
    "the rights of this Paperless user cannot be read (view_uisettings): the originals then stay, "
    "and the result names what was started and how to go on"
)
MAY_NOT_DELETE: Final = (
    "The Paperless user may not delete {which} original, so nothing was started."
)
MAY_NOT_DELETE_HINT: Final = (
    "Trashing needs the permission to delete documents and ownership (or no owner). Keep the "
    "originals, or ask their owner."
)
REPROCESS_NOTE: Final = (
    "Paperless does not say which document a reprocess task belongs to (its tasks name none): "
    "they are the newest reprocess tasks this request started, so they cannot be tied to the "
    "documents."
)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _ids(numbers: Sequence[int]) -> str:
    """``3 and 5`` or ``3, 5 and 8``: two or more numbers."""
    names = [str(n) for n in numbers]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _named(documents: Sequence[DocumentRecord]) -> str:
    """``document 5 'Title'`` for each, joined like ``_ids``."""
    names = [f"document {d.id} {quoted(d.title)}" for d in documents]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


_WORST_FIRST: Final[tuple[UploadState, ...]] = (
    "failure",
    "revoked",
    "unknown",
    "started",
    "pending",
)


def _aggregate(states: Sequence[UploadState]) -> UploadState:
    """The state of several tasks together: the worst one counts, a running one beats done."""
    for state in _WORST_FIRST:
        if state in states:
            return state
    return "success"


@dataclass(frozen=True, slots=True)
class _Source:
    """The file a request for one document reads.

    Attributes:
        version: The version whose file it is.
        older: Whether the document shows a newer one.
        pages: How many pages the file has, if Paperless counted them.
    """

    document: DocumentRecord
    version: int
    older: bool
    pages: int | None

    @property
    def scope(self) -> object:
        """What a confirmation binds of the file."""
        return self.pages, self.document.newest_file, self.version

    def named(self) -> str:
        """The document, and the version if it is not the one the document shows."""
        version = f" (older version {self.version})" if self.older else ""
        return f"{_named([self.document])}{version}"


@dataclass(frozen=True, slots=True)
class _Request:
    """How a page-based request reads in a result and a refusal.

    Attributes:
        operation: The tool's operation name in the result.
        summary: What was asked, for the result.
        what: The same with the document named, for the refusal.
        verb: What dropping pages is called here: drop, or delete for delete_pages.
        parts: The new documents that are reported one by one.
        removed: The pages the document loses once they are in the new document.
    """

    operation: str
    summary: str
    what: str
    verb: str = "drop"
    parts: tuple[Part, ...] = ()
    removed: tuple[int, ...] = ()

    @property
    def single(self) -> bool:
        """Whether its one part is called the new document."""
        return self.operation == "extract_pages"

    @property
    def sets(self) -> bool:
        """Whether new documents get metadata of their own."""
        return any(part.edit is not None for part in self.parts)


@dataclass(frozen=True, slots=True)
class Options:
    """What every operation on the pages of one document takes besides its pages.

    Attributes:
        version_id: The version whose file is read, None for the newest.
        keep_metadata: Whether new documents take the metadata of the document.
        dry_run: Return the plan, start nothing.
        wait_seconds: None waits where the originals are trashed at the end, else not at all.
    """

    version_id: int | None = None
    keep_metadata: bool = True
    confirm: bool = False
    dry_run: bool = False
    wait_seconds: float | None = None


DEFAULTS: Final = Options()


class PdfService:
    """Runs PDF operations for the current user and reports their tasks."""

    def __init__(  # noqa: PLR0913 -- one port or service per concern
        self,
        reader: DocumentReader,
        writer: PdfWriter,
        tasks: TaskService,
        replacing: Replacing,
        taxonomy: TaxonomyResolver,
        *,
        parts: Parts,
        web_url: str,
    ) -> None:
        """Read through ``reader``, write through ``writer``, follow tasks through ``tasks``.

        ``replacing`` verifies and trashes, ``parts`` finds new documents and sets their metadata,
        ``taxonomy`` tells the custom fields; ``web_url`` is the Paperless web UI, for the links of
        a result.
        """
        self._web_url: str = web_url
        self._reader: DocumentReader = reader
        self._writer: PdfWriter = writer
        self._tasks: TaskService = tasks
        self._replacing: Replacing = replacing
        self._parts: Parts = parts
        self._taxonomy: TaxonomyResolver = taxonomy

    async def rotate(  # noqa: PLR0913 -- the documents and the options of the rotation
        self,
        document_ids: Sequence[int],
        degrees: int,
        *,
        use_original_files: bool = False,
        confirm: bool = False,
        dry_run: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Rotate every page of the documents clockwise; each gets a new version.

        ``degrees`` is a multiple of 90 other than 0 (a negative one turns back). With
        ``use_original_files`` the first file is rotated: a document that shows a newer one shows
        the older content again, which needs ``confirm``.

        Raises:
            ConfirmationRequired: A document would show older content again. Nothing was changed.
            Validation: No document, too many, one twice or not a PDF, or a rotation that is not
                a quarter, half or three-quarter turn.
            NotFound: A document does not exist or the user may not see it.
            PermissionDenied: The user may not change a document.
        """
        turn = degrees % 360
        if degrees % 90 or not turn:
            message = f"{degrees} degrees is not a quarter turn."
            raise Validation(message, hint="Use 90, 180 or 270 (clockwise), or -90.")
        documents, _ = await self._documents(document_ids, pdf=True, first=use_original_files)
        back = [d for d in documents if use_original_files and len(d.versions) > 1]
        summary = f"rotate {_plural(len(documents), 'document')} by {turn} degrees clockwise"
        plan = version_plan(
            f"a new version of {_plural(len(documents), 'document')}",
            "the document" if len(documents) == 1 else "each document",
            loses=(
                *((f"{_named(back)} no longer shows its newest file",) if back else ()),
                UNSIGNED,
            ),
            needs_confirm=bool(back),
        )
        if dry_run:
            return self._preview("rotate", summary, document_ids, plan)
        if back and not confirm:
            effect = (
                f"rotate the first file of {_named(back)} as a new version: the document then "
                "shows the older content again, and its newest file stays as an earlier version"
            )
            ConfirmationRequired.require(effect, scope=[d.newest_file for d in documents])
        started = await self._writer.rotate_documents(
            document_ids, turn, original_files=use_original_files
        )
        return await self._result(
            "rotate",
            summary,
            document_ids,
            started,
            plan,
            wait_seconds=wait_seconds,
            version=True,
            copied=documents,
        )

    async def merge(  # noqa: PLR0913 -- the documents and the options of the merge
        self,
        document_ids: Sequence[int],
        metadata_document_id: int | None,
        *,
        delete_originals: bool = False,
        archive_fallback: bool = False,
        use_original_files: bool = False,
        confirm: bool = False,
        dry_run: bool = False,
        wait_seconds: float | None = None,
    ) -> PdfResult:
        """Merge the PDFs, in this order, into one new document.

        The new document takes the metadata of ``metadata_document_id``, one of the documents.
        Deleting the originals moves them to the trash, after the merged document was checked, and
        needs ``confirm``. With ``archive_fallback`` the archive PDF stands in for a file that is
        not a PDF.

        Raises:
            ConfirmationRequired: ``delete_originals`` without ``confirm``. Nothing was changed.
            Validation: Fewer than two documents, a repeat, one Paperless would leave out without
                a word, or a metadata document that is not among them; first files that would
                replace documents showing newer ones.
            NotFound: A document does not exist or the user may not see it.
            PermissionDenied: The user may not change a document.
        """
        if len(document_ids) < 2:  # noqa: PLR2004 -- merging one document is no merge
            message = "Merging needs at least two documents."
            raise Validation(message, hint="Name the documents to merge, in order.")
        if metadata_document_id is not None and metadata_document_id not in document_ids:
            message = f"Document {metadata_document_id} is not among the documents to merge."
            raise Validation(message, hint="Take the metadata from one of the merged documents.")
        documents, facts = await self._documents(
            document_ids, pdf=False, certain=True, first=use_original_files
        )
        newer = next((d for d in documents if use_original_files and len(d.versions) > 1), None)
        if delete_originals and newer is not None:
            raise _newer_lost(newer.id, "use_original_files")
        unread = await self._require_delete(documents) if delete_originals else ()
        counts = [
            await self._mergeable(
                d, facts[d.id], archive_fallback=archive_fallback, first=use_original_files
            )
            for d in documents
        ]
        pages = None if None in counts else sum(n or 0 for n in counts)
        summary = f"merge documents {_ids(document_ids)} into one new document"
        serial = _merged_serial(documents, metadata_document_id)
        serials = numbers(documents, serial, trashes=delete_originals)
        archived = [
            d for d in documents if delete_originals and facts[d.id].original_mime_type != PDF
        ]
        only = tuple(ARCHIVE_ONLY.format(name=_named([d])) for d in archived)
        plan = _merge_plan(
            documents, metadata_document_id, pages, serials, only, trashes=delete_originals
        )
        plan = plan.model_copy(update={"keeps": (*plan.keeps, *unread)})
        if delete_originals:
            summary += " and move the originals to the Paperless trash"
        if dry_run:
            return self._preview("merge", summary, document_ids, plan)
        if delete_originals and not confirm:
            effect = (
                f"merge {_named(documents)} into one new document, in this order, and "
                f"move the originals to the Paperless trash; each {BACK}"
            )
            lost = () if metadata_document_id is not None else (without(_named(documents)),)
            effect = "; ".join([effect, *lost, *only, *serials.keeps, *serials.loses, *unread])
            scope = [(d.newest_file, d.archive_serial_number) for d in documents]
            ConfirmationRequired.require(
                effect, scope=(pages, scope, metadata_document_id, bool(unread))
            )
        began = self._replacing.clock()
        started = await self._writer.merge_documents(
            document_ids,
            metadata_document_id=metadata_document_id,
            archive_fallback=archive_fallback,
            original_files=use_original_files,
        )
        bound = _bound(wait_seconds, composed=delete_originals)
        copied = [d for d in documents if d.id == metadata_document_id]
        started, found = await self._parts.look(started, await self._dates(copied), bound)
        result = await self._result(
            "merge",
            summary,
            document_ids,
            started,
            plan,
            wait_seconds=bound,
            copied=copied,
            found=found,
        )
        left_out = _left_out(document_ids, started)
        if not delete_originals:
            return await self._merged_note(result, left_out, pages)
        if started.included is None and pages is None:
            left_out = "it cannot be told whether every document is in the merged one"
        wanted = Replacement(
            documents, {0: pages}, serial=serial, problem=left_out, unread=bool(unread)
        )
        return await self._replacing.finish(result, started, wanted, began=began)

    async def split(
        self,
        document_id: int,
        parts: Sequence[str],
        *,
        output: Output = "new_document",
        part_metadata: Sequence[PartMetadata] | None = None,
        options: Options = DEFAULTS,
    ) -> PdfResult:
        """Split a PDF into one new document per part, each part a page range like ``1-3,5``.

        Pages in no part are in no new document. ``part_metadata`` is set on each part once it was
        checked. ``replace_original`` moves the original to the trash, after every part was checked
        and has its metadata, and needs ``confirm``.

        Raises:
            ConfirmationRequired: ``replace_original`` without ``confirm``. Nothing was changed.
            Validation: Fewer than two parts or too many, a bad or overlapping range, a page past
                the end, a non-PDF, ``output`` ``new_version``, or not one metadata per part.
            NotFound: No such document or version, or a name of the metadata matches nothing.
            Ambiguous: A name of the metadata matches more than one object.
            PermissionDenied: The user may not change the document.
        """
        if output == "new_version":
            message = "Several files cannot be one version of a document."
            raise Validation(message, hint="Closest: new documents (new_document).")
        if not 2 <= len(parts) <= MAX_PARTS:  # noqa: PLR2004 -- two parts make a split
            message = f"A split needs at least two parts and at most {MAX_PARTS} parts."
            raise Validation(message, hint="Give a page range per new document, e.g. '1-3'.")
        source, count = await self._paged(document_id, options.version_id)
        slots: list[PageSlot] = []
        seen: dict[int, int] = {}
        listed: list[list[int]] = []
        for number, spec in enumerate(parts):
            pages = parse_pages(spec, page_count=count)
            for page in pages:
                if page in seen:
                    message = f"Page {page} is in part {seen[page] + 1} and part {number + 1}."
                    raise Validation(message, hint="Give each page to one part.")
                seen[page] = number
            listed.append(pages)
            slots.extend(PageSlot(page=page, document=number) for page in pages)
        made = await self._parts.resolve(listed, part_metadata)
        ranges = [part.label for part in made]
        summary = f"split document {document_id} into {len(parts)} new documents: "
        summary += " | ".join(ranges)
        what = f"split {source.named()} into {len(parts)} new documents ({' | '.join(ranges)})"
        request = _Request("split", summary, what, parts=made)
        return await self._send_pages(source, slots, request, output=output, options=options)

    async def extract(
        self,
        document_id: int,
        pages: str,
        *,
        remove: bool = False,
        metadata: PartMetadata | None = None,
        options: Options = DEFAULTS,
    ) -> PdfResult:
        """Put pages (a range like ``2-3,5``, in this order) into one new document.

        With ``remove`` the document then gets a new version without them, once the new document
        was checked and has its ``metadata``; that needs ``confirm``.

        Raises:
            ConfirmationRequired: ``remove`` without ``confirm``. Nothing was changed.
            Validation: A bad range, a page past the end, a non-PDF, every page or an older version
                with ``remove``, or a bad title.
            NotFound: No such document or version, or a name of the metadata matches nothing.
            Ambiguous: A name of the metadata matches more than one object.
            PermissionDenied: The user may not change the document.
        """
        source, count = await self._paged(document_id, options.version_id)
        taken = parse_pages(pages, page_count=count)
        if remove and len(taken) == count:
            message = "Removing every page leaves nothing."
            raise Validation(message, hint="The document itself holds exactly these pages.")
        if remove and source.older:
            message = (
                f"Document {document_id} has newer versions, so pages are removed from the newest "
                "only."
            )
            raise Validation(message, hint="Extract without remove, or leave version_id out.")
        [part] = await self._parts.resolve([taken], None if metadata is None else [metadata])
        where = f"extract {part.label} of document {document_id} into a new document"
        request = _Request(
            "extract_pages",
            f"{where} and remove them from it" if remove else where,
            f"extract {part.label} of {source.named()} into a new document",
            parts=(part,),
            removed=tuple(sorted(taken)) if remove else (),
        )
        slots = [PageSlot(page=page) for page in taken]
        return await self._send_pages(
            source, slots, request, output="new_document", options=options
        )

    async def delete_pages(
        self,
        document_id: int,
        pages: str,
        *,
        output: Output = "new_version",
        options: Options = DEFAULTS,
    ) -> PdfResult:
        """Make a file without the given pages (a range like ``2-3,5``), in order.

        As a new version (the default) the document shows the shorter file and the earlier version
        stays; ``confirm`` is needed, as for ``replace_original`` (new document, the original goes
        to the trash). Into a ``new_document`` nothing existing changes.

        Raises:
            ConfirmationRequired: The output changes the document and ``confirm`` is false.
            Validation: A bad range, a page past the end, every page deleted, or a non-PDF.
            NotFound: No such document or version.
            PermissionDenied: The user may not change the document.
        """
        source, count = await self._paged(document_id, options.version_id)
        deleted = parse_pages(pages, page_count=count)
        kept = [page for page in range(1, count + 1) if page not in deleted]
        if not kept:
            message = "Deleting every page leaves nothing."
            raise Validation(message, hint="Delete the whole document with delete_document.")
        where = "as a new version" if output == "new_version" else "into a new document"
        request = _Request(
            "delete_pages",
            f"delete {describe(deleted)} of document {document_id} {where}",
            f"delete {describe(deleted)} of {source.named()} {where}",
            verb="delete",
        )
        slots = [PageSlot(page=page) for page in kept]
        return await self._send_pages(source, slots, request, output=output, options=options)

    async def edit(
        self,
        document_id: int,
        pages: Sequence[PageSlot],
        *,
        output: Output = "new_document",
        options: Options = DEFAULTS,
    ) -> PdfResult:
        """Build files from the pages of one PDF: pick, order, turn and group them.

        Raises:
            ConfirmationRequired: The output drops pages from what the document shows, goes back
                to an older file, or trashes the original, and ``confirm`` is false. Nothing was
                changed.
            Validation: No page or too many, a page that does not exist, a rotation that is not a
                multiple of 90, output files not numbered from 0 without gaps, several files as
                a new version, or a non-PDF.
            NotFound: No such document or version.
            PermissionDenied: The user may not change the document.
        """
        if not pages or len(pages) > MAX_PAGES:
            message = f"Name at least one page and at most {MAX_PAGES} pages."
            raise Validation(message, hint="Each page of the result is one entry.")
        source, count = await self._paged(document_id, options.version_id)
        for slot in pages:
            if slot.rotate % 90:
                message = f"A rotation must be a multiple of 90 degrees, not {slot.rotate}."
                raise Validation(message, hint="Use 0, 90, 180 or 270.")
            _ = parse_pages(str(slot.page), page_count=count)
        files = sorted({slot.document for slot in pages})
        if files[0] < 0:
            message = "Output files are numbered from 0."
            raise Validation(message, hint="Use 0 for the first file, 1 for the second.")
        if files != list(range(len(files))):
            message = "Output files must be numbered 0, 1, 2 and so on without gaps."
            raise Validation(message, hint="Renumber the files so that none is skipped.")
        if output == "new_version" and len(files) > 1:
            message = "A new version is a single file, but the pages go to several files."
            raise Validation(message, hint="Use new_document or replace_original.")
        turned = sum(1 for slot in pages if slot.rotate % 360)
        shape = f"{_plural(len(pages), 'page')}, {turned} turned"
        where = (
            "as a new version"
            if output == "new_version"
            else f"into {_plural(len(files), 'new document')}"
        )
        request = _Request(
            "edit_pdf",
            f"edit document {document_id} {where} ({shape})",
            f"edit {source.named()} {where} ({shape})",
        )
        return await self._send_pages(source, list(pages), request, output=output, options=options)

    async def remove_password(
        self,
        document_id: int,
        password: str,
        *,
        output: Output = "new_document",
        options: Options = DEFAULTS,
    ) -> PdfResult:
        """Unlock a password protected PDF; the result goes to ``output``.

        ``replace_original`` moves the locked file to the trash, after the unlocked one was
        checked, and needs ``confirm``. A new version keeps the locked one as an older version. A
        file without a password starts no task.

        Raises:
            ConfirmationRequired: ``replace_original``, or an older version as the newest one,
                without ``confirm``. Nothing was changed.
            Validation: An empty or wrong password, or a non-PDF.
            NotFound: No such document or version.
            PermissionDenied: The user may not change the document.
        """
        if not password:
            message = "The password is empty."
            raise Validation(message, hint="Give the password that opens the file.")
        source = await self._source(document_id, options.version_id)
        where = {
            "new_document": "into a new document",
            "new_version": "as a new version",
            "replace_original": "into a new document and move the original to the Paperless trash",
        }[output]
        summary = f"remove the password of document {document_id} {where}"
        what = f"remove the password of {source.named()} {where}"
        return await self._run(
            source,
            _Request("remove_password", summary, what),
            {0: None},
            output=output,
            options=options,
            start=lambda wanted: self._writer.remove_password(
                document_id,
                password,
                output=wanted,
                version_id=source.version,
                keep_metadata=options.keep_metadata,
            ),
            no_task=UNLOCKED_NONE,
        )

    async def reprocess(
        self,
        document_ids: Sequence[int],
        *,
        remote_ocr: bool = False,
        confirm: bool = False,
        dry_run: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Run OCR and archiving again; the archived file and the text are replaced.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            Validation: No document, too many, or one twice.
            NotFound: A document does not exist or the user may not see it.
            PermissionDenied: The user may not change a document.
        """
        documents, _ = await self._documents(document_ids, pdf=False)
        engine = "remote OCR" if remote_ocr else "local OCR"
        summary = f"reprocess {_plural(len(documents), 'document')} ({engine})"
        plan = PdfPlan(
            produces="nothing new: the archived PDF and the text of the newest version again",
            keeps=("the original file, earlier versions and every metadata",),
            loses=("the archived PDF, the extracted text and manual changes to the text",),
            needs_confirm=True,
        )
        if dry_run:
            return self._preview("reprocess", summary, document_ids, plan)
        if not confirm:
            effect = (
                f"reprocess {_named(documents)} (OCR and archiving run again; the archived PDF "
                "and the extracted text are replaced, so manual changes to the text are lost)"
            )
            ConfirmationRequired.require(effect, scope=[d.newest_file for d in documents])
        started = await self._writer.reprocess_documents(document_ids, remote_ocr=remote_ocr)
        return await self._result(
            "reprocess",
            summary,
            document_ids,
            started,
            plan,
            wait_seconds=wait_seconds,
            note=REPROCESS_NOTE,
        )

    async def _documents(
        self,
        document_ids: Sequence[int],
        *,
        pdf: bool,
        certain: bool = False,
        version_id: int | None = None,
        first: bool = False,
    ) -> tuple[list[DocumentRecord], dict[int, FileMetadata]]:
        """The documents and what could be read of their files, after the checks without a write.

        Each must be ours to change. The file checked is the newest, the first or that of
        ``version_id``. ``certain`` is for a merge, where a missing file would be left out without
        a word; the others would only fail.
        """
        if not document_ids:
            message = "Name at least one document."
            raise Validation(message, hint="Pass the document ids from search_documents.")
        if len(document_ids) > MAX_DOCUMENTS:
            message = f"Work on at most {MAX_DOCUMENTS} documents at once; got {len(document_ids)}."
            raise Validation(message, hint="Work in several steps.")
        repeated = next((n for n in document_ids if document_ids.count(n) > 1), None)
        if repeated is not None:
            message = f"Document {repeated} is listed twice."
            raise Validation(message, hint="List each document once.")
        found: list[DocumentRecord] = []
        versions: dict[int, int | None] = {}
        for number in document_ids:
            document = await self._reader.get_document(number)
            require_change(document)
            wanted = listed_version(document, number if first else version_id)
            versions[number] = None if wanted == listed_version(document, None) else wanted
            found.append(document)
        facts = await require_files(self._reader, versions, "original", certain=certain)
        for document in found if pdf else ():
            file = facts.get(document.id)
            kind = document.mime_type if file is None else file.original_mime_type
            if kind != PDF:
                message = f"Document {document.id} is not a PDF ({kind})."
                raise Validation(message, hint="Only PDF files can be changed this way.")
        return found, facts

    async def _mergeable(
        self, document: DocumentRecord, file: FileMetadata, *, archive_fallback: bool, first: bool
    ) -> int | None:
        """The pages a document brings to a merge, which must not leave it out without a word."""
        number, kind = document.id, file.original_mime_type
        pages = await self._pages(document, number if first else None)
        if kind == PDF:
            if pages is None:
                message = (
                    f"Document {number} cannot be merged: Paperless counted no pages of its file."
                )
                raise Validation(message, hint=NO_PAGES_HINT)
            return pages
        if not archive_fallback:
            message = f"Document {number} is not a PDF ({kind})."
            hint = "archive_fallback merges the archive PDF of other files, where there is one."
            raise Validation(message, hint=hint)
        if not file.has_archive_version or file.lacks("archive"):
            message = f"Document {number} is not a PDF ({kind}) and has no archive PDF to merge."
            raise Validation(message, hint="Leave the document out.")
        return pages

    async def _pages(self, document: DocumentRecord, version_id: int | None) -> int | None:
        """The page count of a version of the document (None: the newest)."""
        if version_id is None or version_id == listed_version(document, None):
            return document.page_count
        return await self._reader.get_version_pages(document.id, version_id)

    async def _source(self, document_id: int, version_id: int | None) -> _Source:
        """The PDF a request for one document reads."""
        [document], _ = await self._documents([document_id], pdf=True, version_id=version_id)
        version = listed_version(document, version_id)
        return _Source(
            document=document,
            version=version,
            older=version != listed_version(document, None),
            pages=await self._pages(document, version),
        )

    async def _paged(self, document_id: int, version_id: int | None) -> tuple[_Source, int]:
        """The PDF whose pages are named, and its page count, which must be known."""
        source = await self._source(document_id, version_id)
        if source.pages is None:
            message = f"The page count of document {document_id} is unknown."
            raise Validation(
                message,
                hint=NO_PAGES_HINT,
            )
        return source, source.pages

    async def _send_pages(
        self,
        source: _Source,
        slots: list[PageSlot],
        request: _Request,
        *,
        output: Output,
        options: Options,
    ) -> PdfResult:
        """The common end of split, delete_pages and edit: which pages go where is settled."""
        files = Counter(slot.document for slot in slots)
        used = {slot.page for slot in slots}
        dropped = [page for page in range(1, (source.pages or 0) + 1) if page not in used]
        return await self._run(
            source,
            request,
            files,
            output=output,
            options=options,
            start=lambda wanted: self._writer.edit_pdf(
                source.document.id,
                slots,
                output=wanted,
                version_id=source.version,
                keep_metadata=options.keep_metadata,
            ),
            dropped=dropped,
        )

    async def _run(  # noqa: PLR0913 -- the pieces of one request for one document
        self,
        source: _Source,
        request: _Request,
        files: Mapping[int, int | None],
        *,
        output: Output,
        options: Options,
        start: Callable[[FileOutput], Awaitable[StartedTasks]],
        dropped: Sequence[int] = (),
        no_task: str = "",
    ) -> PdfResult:
        """Plan, confirm what needs it, start, and where originals are replaced finish the work.

        ``files`` is how many pages each output file gets, ``dropped`` the pages none gets.
        """
        document = source.document
        trashes, version = output == "replace_original", output == "new_version"
        if version and not options.keep_metadata:
            message = "A new version has no metadata of its own: the document keeps its metadata."
            raise Validation(message, hint="Leave keep_metadata out, or use new_document.")
        if trashes and source.older:
            raise _newer_lost(document.id, "version_id")
        unread = await self._require_delete([document]) if trashes else ()
        single = len(files) == 1 and request.operation != "remove_password"
        moves = document if single else None
        serials = numbers([document], moves, trashes=trashes)
        plan = _file_plan(
            source, request, len(files), dropped, serials, output=output, options=options
        )
        sets = part_lines(request.parts, single=request.single)
        plan = plan.model_copy(update={"keeps": (*plan.keeps, *unread, *sets)})
        summary = request.summary
        if trashes and request.operation != "remove_password":
            summary += f" and {TRASH}"
        if options.dry_run:
            return self._preview(request.operation, summary, [document.id], plan)
        if plan.needs_confirm and not options.confirm:
            asked = _effect(source, request, dropped, serials, (*unread, *sets), options, output)
            scope = (
                source.scope,
                document.archive_serial_number if trashes else None,
                options.keep_metadata,
                bool(unread),
                [part.edit for part in request.parts],
            )
            ConfirmationRequired.require(asked, scope=scope)
        began = self._replacing.clock()
        started = await start("new_version" if version else "new_document")
        composed = trashes or request.sets or bool(request.removed)
        bound = _bound(options.wait_seconds, composed=composed)
        copied = [document] if options.keep_metadata and not version else []
        started, found = await self._parts.look(started, await self._dates(copied), bound)
        result = await self._result(
            request.operation,
            summary,
            [document.id],
            started,
            plan,
            wait_seconds=bound,
            version=version,
            no_task=no_task,
            copied=copied,
            found=found,
        )
        unset = None
        if request.parts:
            made = await self._parts.settle(
                result, started, request.parts, files, document, began=began
            )
            unset = unset_reason(made, single=request.single)
            result = result.model_copy(update={"parts": made, "original": "kept"})
        wanted = Replacement(
            [document],
            files,
            serial=moves if trashes else None,
            unread=bool(unread),
            perhaps_none=bool(no_task) and DATED not in (result.message or ""),
            unset=unset,
            first=EVERY_PART if unset else "",
        )
        if trashes:
            return await self._replacing.finish(result, started, wanted, began=began)
        if request.removed:
            return await self._remove(
                source, request, result, started, wanted, began=began, bound=bound
            )
        notes = [] if unset is None else [f"{unset[0].upper()}{unset[1:]}."]
        return noted(result, notes, next_call=None)

    async def _remove(  # noqa: PLR0913 -- the new document and what it was made of
        self,
        source: _Source,
        request: _Request,
        result: PdfResult,
        started: StartedTasks,
        wanted: Replacement,
        *,
        began: float,
        bound: float,
    ) -> PdfResult:
        """Remove the pages from the document as a new version, if the new document holds them.

        Nothing is raised any more: the new document exists.
        """
        document = source.document
        spec = specification(request.removed)
        call = f'delete_pages(document_id={document.id}, pages="{spec}", output="new_version")'
        again = f"{if_unchanged([document])}: {call}"
        wanted = replace(wanted, then=call, kept=NOT_REMOVED.format(id=document.id))
        drop = ""
        try:
            product = await self._replacing.checked(result, started, wanted, began=began)
            drop = f"; or delete_document(document_id={product.id}) to drop the new document"
            await self._replacing.unchanged(document, document.archive_serial_number)
        except KeptError as kept:
            look = f"get_document(document_id={document.id}), then {call} if the pages are still "
            next_call = f"{look}these{drop}" if drop else kept.next_call
            return stays(result, wanted, kept.reason, next_call)
        rest = [
            PageSlot(page=page)
            for page in range(1, (source.pages or 0) + 1)
            if page not in request.removed
        ]
        unknown = f'get_document(document_id={document.id}, include=["versions"]), {again}'
        try:
            second = await self._writer.edit_pdf(
                document.id, rest, output="new_version", version_id=source.version
            )
        except (*REFUSALS, NotFound) as error:
            return stays(result, wanted, cause(error), f"{again}{drop}")
        except PaperlessMcpError as error:
            note = MAYBE_REMOVED.format(id=document.id, cause=cause(error), product=product.id)
            known = {"state": "unknown", "original": "unknown"}
            return noted(result, [note], next_call=unknown).model_copy(update=known)
        removal = await self._result(
            request.operation,
            result.summary,
            [document.id],
            second,
            result.plan,
            wait_seconds=max(bound - result.waited_seconds, 0),
            version=True,
        )
        result = _together(result, removal)
        if removal.state == "success" and removal.produced_ids:
            return result.model_copy(update={"original": "shortened"})
        if removal.state in {"success", "failure", "revoked"}:
            return stays(result, wanted, "its task made no version", f"{again}{drop}")
        waits = [f'get_task(task_id="{task.task_id}", wait_seconds=30)' for task in removal.tasks]
        known = {"original": "unknown", "next": waits[0] if waits else unknown}
        return result.model_copy(update=known)

    async def _merged_note(
        self, result: PdfResult, left_out: str | None, pages: int | None
    ) -> PdfResult:
        """``result``, saying what a merge is known to lack."""
        notes = [f"{left_out[0].upper()}{left_out[1:]}."] if left_out else []
        for number in result.produced_ids:
            with contextlib.suppress(PaperlessMcpError):
                made = (await self._reader.get_document(number)).page_count
                if pages is not None and made != pages:
                    notes.append(f"Document {number} has {made} pages instead of {pages}.")
        message = " ".join([*([result.message] if result.message else []), *notes]) or None
        return result.model_copy(update={"message": message})

    def _preview(
        self, operation: str, summary: str, sources: Sequence[int], plan: PdfPlan
    ) -> PdfResult:
        """The result of a dry run: nothing was started."""
        return PdfResult(
            operation=operation,
            summary=summary,
            dry_run=True,
            plan=plan,
            source_document_ids=tuple(sources),
            source_links=tuple(document_link(self._web_url, n) for n in sources),
            state="not_started",
            message=None,
            tasks=(),
            produced_ids=(),
            produced_links=(),
            trashed_ids=(),
            next=None,
            wait="not_requested",
            waited_seconds=0,
        )

    async def _result(  # noqa: PLR0913 -- the pieces of one result
        self,
        operation: str,
        summary: str,
        sources: Sequence[int],
        started: StartedTasks,
        plan: PdfPlan,
        *,
        wait_seconds: float,
        version: bool = False,
        no_task: str = "",
        copied: Sequence[DocumentRecord] = (),
        note: str = "",
        found: Found | None = None,
    ) -> PdfResult:
        """Follow the tasks for up to ``wait_seconds`` altogether and report them.

        ``version``: the result is a new version of the source (the produced ids are version ids,
        so the links are the sources'). ``no_task`` explains why a request may have no task,
        ``copied`` are the documents whose metadata is copied, ``note`` is added to the message.
        ``found`` are the new documents in place of tasks Paperless does not list.
        """
        bound = min(max(wait_seconds, 0), MAX_WAIT_SECONDS)
        tasks: list[PdfTask] = [] if found is None else list(found.tasks)
        waited = 0.0 if found is None else found.waited
        for task_id in started.ids:
            outcome = await follow(
                self._tasks, task_id, max(bound - waited, 0), accepted="the request"
            )
            waited += outcome.waited_seconds
            tasks.append(
                PdfTask(
                    task_id=task_id,
                    state=outcome.state,
                    message=outcome.message,
                    document_ids=outcome.document_ids,
                )
            )
        states: list[UploadState] = [task.state for task in tasks]
        complete = started.problem is None and len(tasks) == started.expected
        notes = [task.message for task in tasks if task.message][:1]
        notes += await self._lookup_notes(
            started, tasks, copied, no_task, by_name=found is not None
        )
        if note:
            notes.append(note)
        wait: Wait = "not_requested"
        if tasks and bound > 0:
            wait = "finished" if all(s in FINAL for s in states) else "timed_out"
        produced = tuple(n for task in tasks for n in task.document_ids)
        links = tuple(document_link(self._web_url, n) for n in sources)
        return PdfResult(
            operation=operation,
            summary=summary,
            dry_run=False,
            plan=plan,
            source_document_ids=tuple(sources),
            source_links=links,
            state=_aggregate(states) if complete else "unknown",
            message=" ".join(notes) or None,
            tasks=tuple(tasks),
            produced_ids=produced,
            produced_links=self._produced_links(sources, tasks, started, version=version),
            trashed_ids=(),
            next=None,
            wait=wait,
            waited_seconds=round(waited, 1),
        )

    async def _lookup_notes(
        self,
        started: StartedTasks,
        tasks: Sequence[PdfTask],
        copied: Sequence[DocumentRecord],
        no_task: str,
        *,
        by_name: bool,
    ) -> list[str]:
        """What the result says of the search for its tasks."""
        if started.problem is not None:
            unconfirmed = UNCONFIRMED.format(detail=started.detail)
            said = {"forbidden": FORBIDDEN, "failed": UNREADABLE, "unconfirmed": unconfirmed}
            return [said[started.problem]]
        if by_name:
            return [BY_NAME]
        if not tasks:
            none = NONE_FOUND.format(reason=await self._why_hidden(copied, no_task))
            doubt = started.doubt
            return [none, *(() if doubt is None else (NOT_TAKEN.format(doubt=doubt),))]
        if len(tasks) == started.expected:
            return []
        reason = await self._why_hidden(copied)
        those = "that one" if len(tasks) == 1 else "those"
        return [
            SOME_FOUND.format(
                found=len(tasks), expected=started.expected, those=those, reason=reason
            )
        ]

    async def _why_hidden(self, copied: Sequence[DocumentRecord], other: str = "") -> str:
        """Why a task may be missing: ``other``, or a date among the custom fields that are copied.

        3.5.1 cannot record a task whose metadata holds such a date.
        """
        causes = [other] if other else []
        if await self._dates(copied):
            causes.append(DATED)
        return f" ({', or '.join(causes)})" if causes else ""

    async def _dates(self, copied: Sequence[DocumentRecord]) -> dict[int, object]:
        """The dates among the custom fields that are copied, by field."""
        given = {v.field: v.value for d in copied for v in d.custom_fields if v.value is not None}
        with contextlib.suppress(PaperlessMcpError):
            fields = await self._taxonomy.items("custom_field") if given else []
            dates = {field.id for field in fields if field.data_type == "date"}
            return {number: value for number, value in given.items() if number in dates}
        return {}

    async def _require_delete(self, documents: Sequence[DocumentRecord]) -> tuple[str, ...]:
        """Refuse early when the user is known not to be allowed to trash the originals.

        Returns what the plan has to say where that cannot be known.

        Raises:
            PermissionDenied: The user lacks the permission, or does not own one of them.
        """
        allowed = await self._reader.may_delete([d.owner for d in documents])
        if allowed is False:
            message = MAY_NOT_DELETE.format(which="the" if len(documents) == 1 else "every")
            raise PermissionDenied(message, hint=MAY_NOT_DELETE_HINT)
        return () if allowed else (UNREAD_RIGHTS,)

    def _produced_links(
        self,
        sources: Sequence[int],
        tasks: Sequence[PdfTask],
        started: StartedTasks,
        *,
        version: bool,
    ) -> tuple[str, ...]:
        """Links to what the tasks made.

        A new version is made on its source, which gets the link: the document its own task
        names (so a task that was not found takes nothing from another's), or the only source
        when the request has one. Where neither is known there is no link.
        """
        links: list[str] = []
        for index, task in enumerate(tasks):
            if not task.document_ids:
                continue
            if not version:
                links.extend(document_link(self._web_url, n) for n in task.document_ids)
                continue
            known = started.sources[index] if index < len(started.sources) else None
            source = known if known is not None else sources[0] if len(sources) == 1 else None
            if source is not None:
                links.append(document_link(self._web_url, source))
        return tuple(links)


def _effect(  # noqa: PLR0913, PLR0917 -- what the words depend on
    source: _Source,
    request: _Request,
    dropped: Sequence[int],
    serials: Numbers,
    more: Sequence[str],
    options: Options,
    output: Output,
) -> str:
    """What confirming would do."""
    lost = () if options.keep_metadata else (without(_named([source.document])),)
    if output == "new_version":
        return _version_effect(source, request, dropped)
    if request.removed:
        return "; ".join([f"{request.what} {VANISH}", *lost, *more])
    what = request.what
    if request.operation != "remove_password":
        what += f" and {TRASH}"
    lost = (*_dropped(request, dropped), *lost, *serials.keeps, *serials.loses)
    return "; ".join([what, f"it {BACK}", *lost, *more])


def _together(made: PdfResult, removal: PdfResult) -> PdfResult:
    """One result of making the new document and removing its pages from the source."""
    messages = [text for text in (made.message, removal.message) if text]
    done = made.wait == removal.wait == "finished"
    return made.model_copy(
        update={
            "state": _aggregate([s for s in (made.state, removal.state) if s != "not_started"]),
            "message": " ".join(messages) or None,
            "tasks": (*made.tasks, *removal.tasks),
            "produced_ids": (*made.produced_ids, *removal.produced_ids),
            "produced_links": (*made.produced_links, *removal.produced_links),
            "wait": "finished" if done else "timed_out",
            "waited_seconds": round(made.waited_seconds + removal.waited_seconds, 1),
        }
    )


def _bound(wait_seconds: float | None, *, composed: bool) -> float:
    """How long to wait: by default only where something is left to do afterwards."""
    if wait_seconds is None:
        return MAX_WAIT_SECONDS if composed else 0
    return wait_seconds


def _newer_lost(number: int, parameter: str) -> Validation:
    message = (
        f"Document {number} has newer versions, which the new document would not hold, so it is "
        "not moved to the trash for it."
    )
    return Validation(message, hint=f"Leave the original where it is, or leave {parameter} out.")


def _left_out(document_ids: Sequence[int], started: StartedTasks) -> str | None:
    """What a merge is known to have left out by the name of its file."""
    if started.included is None:
        return None
    missing = [n for n in document_ids if n not in started.included]
    if not missing:
        return None
    names = f"document {missing[0]}" if len(missing) == 1 else f"documents {_ids(missing)}"
    return f"Paperless left {names} out"


def _merged_serial(
    documents: Sequence[DocumentRecord], metadata_document_id: int | None
) -> DocumentRecord | None:
    """Whose serial number the merged document takes: the metadata document's, else the first."""
    numbered = [d for d in documents if d.archive_serial_number is not None]
    preferred = [d for d in numbered if d.id == metadata_document_id]
    return next(iter((*preferred, *numbered)), None)


def _merge_plan(  # noqa: PLR0913 -- what the plan of a merge depends on
    documents: Sequence[DocumentRecord],
    metadata_document_id: int | None,
    pages: int | None,
    serials: Numbers,
    only: tuple[str, ...],
    *,
    trashes: bool,
) -> PdfPlan:
    size = "" if pages is None else f" with {_plural(pages, 'page')}"
    plan = document_plan(
        f"1 new document{size}",
        _named(documents),
        serials,
        loses=(*only, REBUILT),
        trashes=trashes,
        keep_metadata=metadata_document_id is not None,
        metadata_of=None if metadata_document_id is None else f"document {metadata_document_id}",
        title=" (its title with ' (merged)')",
    )
    version = (
        "Paperless always makes a new document from a merge. To make it a version, merge, then "
        "merge_documents_as_versions (toolset versions): the merged document is absorbed for "
        "good, with its title, tags and notes"
    )
    return plan.model_copy(update={"loses": (*plan.loses, version)})


def _file_plan(  # noqa: PLR0913 -- what the plan of a request for one document depends on
    source: _Source,
    request: _Request,
    files: int,
    dropped: Sequence[int],
    serials: Numbers,
    *,
    output: Output,
    options: Options,
) -> PdfPlan:
    """The plan of an operation on one document."""
    name = _named([source.document])
    made = outputs(files, output)
    if request.removed:
        dropped = ()
    loses = _dropped(request, dropped)
    if request.operation != "remove_password":
        loses = (*loses, REBUILT)
    if output == "new_version":
        if source.older:
            loses = (*loses, f"{name} no longer shows its newest file")
        return version_plan(made, name, loses=loses, needs_confirm=bool(dropped) or source.older)
    plan = document_plan(
        made,
        name,
        serials,
        loses=loses,
        keep_metadata=options.keep_metadata,
        trashes=output == "replace_original",
    )
    return shortening(plan, name) if request.removed else plan


def _dropped(request: _Request, dropped: Sequence[int]) -> tuple[str, ...]:
    """The line for the pages that go nowhere."""
    where = "in no new document" if request.operation == "split" else "not in the result"
    return (f"{describe(dropped)}: {where}",) if dropped else ()


def _version_effect(source: _Source, request: _Request, dropped: Sequence[int]) -> str:
    """What a new version would change of what the document shows."""
    name = _named([source.document])
    older = (
        f"make a new version of {name} from its older version {source.version}: the document "
        "then shows that content again, and its newest file stays as an earlier version"
    )
    if not dropped:
        return older
    if source.older:
        return f"{request.verb} {describe(dropped)} and {older}"
    return (
        f"{request.verb} {describe(dropped)} of {name}: the document then shows a new version "
        "without them, and the earlier version with all pages stays until that version is deleted"
    )
