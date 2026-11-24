"""The end of a PDF operation that replaces documents: verify, then trash (decision-60).

Paperless could trash the originals itself, but on the success of the tasks alone, and a task also
succeeds when it stops without a document. So this server does it, as the last step, and only if
every expected document exists, can be read and has the pages it should have, the originals are
still the ones that were accepted, and there is time left. Otherwise the originals stay, the result
says why, and ``next`` names the calls that finish the work. Nothing is remembered between calls.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import DocumentPatch
from mcp_server_paperless.domain.errors import PaperlessMcpError
from mcp_server_paperless.services.removal import WAY_BACK

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord
    from mcp_server_paperless.domain.pdf import PdfResult, StartedTasks
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.services.removal import DocumentRemoval

# No step of a composed call starts later than this after its beginning.
LATEST_START: Final = 45.0
KEPT: Final = "Nothing was moved to the trash: {reason}."
HAS_ONE: Final = (
    "Document {product} has the archive serial number {held} already, so document {original} keeps "
    "{number}."
)
NOT_MOVED: Final = (
    "archive serial number {number} could not be moved to document {product} ({cause})"
)
NOT_BACK: Final = (
    "Archive serial number {number} could not be put back on document {original} either: look at "
    "documents {original} and {product}, and set it with update_document."
)
FIND: Final = (
    "find the new documents with search_documents (newest added first) and check them, then "
)
RUNNING: Final = frozenset({"pending", "started", "unknown"})


@dataclass(frozen=True, slots=True)
class Replacement:
    """What has to be true before originals are trashed.

    Attributes:
        originals: The documents as they were accepted.
        pages: How many pages each output file must have (by its number, from 0); None: any
            number, but Paperless must have counted them.
        serial: The original whose archive serial number moves to the only new document.
        problem: What is already known to be wrong with what was made.
    """

    originals: Sequence[DocumentRecord]
    pages: Mapping[int, int | None]
    serial: DocumentRecord | None = None
    problem: str | None = None


class _KeptError(Exception):
    """Why the originals are kept, and what to call to go on."""

    def __init__(self, reason: str, next_call: str | None = None) -> None:
        super().__init__(reason)
        self.reason: str = reason
        self.next_call: str | None = next_call


@dataclass(slots=True)
class _Done:
    """What the last step did: the originals that went, and what is to be told."""

    trashed: list[int] = field(default_factory=list[int])
    notes: list[str] = field(default_factory=list[str])
    next: str | None = None


def _named(numbers: Sequence[int]) -> str:
    noun = "document" if len(numbers) == 1 else "documents"
    return f"{noun} {', '.join(str(n) for n in numbers)}"


def _deletions(originals: Sequence[DocumentRecord]) -> str:
    return ", then ".join(f"delete_document(document_id={d.id})" for d in originals)


def _cause(error: PaperlessMcpError) -> str:
    return error.message.rstrip(".")


def _check_tasks(result: PdfResult, started: StartedTasks, wanted: Replacement) -> None:
    """Every task must be known, waited for and successful."""
    deletions = _deletions(wanted.originals)
    if started.problem is not None or len(result.tasks) != len(wanted.pages):
        reason = "what Paperless makes could not be followed"
        raise _KeptError(reason, FIND + deletions)
    waits = [
        f'get_task(task_id="{task.task_id}", wait_seconds=30)'
        for task in result.tasks
        if task.state in RUNNING
    ]
    following = ", then ".join([*waits, deletions])
    if result.wait == "not_requested":
        reason = "wait_seconds is 0, so nothing was checked"
        raise _KeptError(reason, following)
    if waits:
        reason = "Paperless is not known to be done"
        raise _KeptError(reason, following)
    if result.state != "success":
        reason = "a task failed"
        raise _KeptError(reason)
    if wanted.problem is not None:
        raise _KeptError(wanted.problem)


class Replacing:
    """Verifies what an operation made and trashes the originals, for the current user."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        removal: DocumentRemoval,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Read through ``reader``, move serial numbers through ``writer``, trash with ``removal``.

        ``clock`` bounds how late the last step may start.
        """
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._removal: DocumentRemoval = removal
        self.clock: Callable[[], float] = clock

    async def finish(
        self, result: PdfResult, started: StartedTasks, wanted: Replacement, *, began: float
    ) -> PdfResult:
        """``result`` after the last step: originals trashed, or why not and how to go on."""
        try:
            _check_tasks(result, started, wanted)
            product = await self._product(result, started, wanted)
            await self._unchanged(wanted.originals, began)
        except _KeptError as kept:
            notes = [KEPT.format(reason=kept.reason)]
            if result.produced_ids:
                notes.append(f"What was made stays: {_named(result.produced_ids)}.")
            return _noted(result, notes, next_call=kept.next_call)
        # Not to be cut short between taking a serial number from one document and giving it on.
        done = await asyncio.shield(asyncio.ensure_future(self._trash(wanted, product)))
        result = _noted(result, done.notes, next_call=done.next)
        return result.model_copy(update={"trashed_ids": tuple(done.trashed)})

    async def _product(
        self, result: PdfResult, started: StartedTasks, wanted: Replacement
    ) -> DocumentRecord:
        """The last of the new documents, all of which must be what they should be."""
        numbers = started.outputs or tuple(range(len(result.tasks)))
        made: list[DocumentRecord] = []
        for task, output in zip(result.tasks, numbers, strict=True):
            if len(task.document_ids) != 1:
                found = _named(task.document_ids) if task.document_ids else "nothing"
                reason = f"task {task.task_id} made {found} instead of one document"
                raise _KeptError(reason)
            [number] = task.document_ids
            try:
                document = await self._reader.get_document(number)
            except PaperlessMcpError as error:
                reason = f"document {number} could not be read ({_cause(error)})"
                raise _KeptError(reason, _deletions(wanted.originals)) from None
            count, pages = document.page_count, wanted.pages[output]
            if count is None or pages not in {None, count}:
                has = "an unknown number of" if count is None else str(count)
                asked = "" if pages is None else f" instead of {pages}"
                reason = f"document {number} has {has} pages{asked}"
                raise _KeptError(reason)
            made.append(document)
        return made[-1]

    async def _unchanged(self, originals: Sequence[DocumentRecord], began: float) -> None:
        """The originals must still be the ones that were accepted, and there must be time left."""
        if self.clock() - began > LATEST_START:
            reason = "the call took too long, though everything was made and checked"
            raise _KeptError(reason, _deletions(originals))
        for original in originals:
            try:
                now = await self._reader.get_document(original.id)
            except PaperlessMcpError as error:
                reason = f"document {original.id} could not be read again ({_cause(error)})"
                raise _KeptError(reason) from None
            if (now.newest_file, now.archive_serial_number) != (
                original.newest_file,
                original.archive_serial_number,
            ):
                reason = f"document {original.id} was changed meanwhile; look at it again"
                raise _KeptError(reason)

    async def _trash(self, wanted: Replacement, product: DocumentRecord) -> _Done:
        """Move the serial number, then the originals to the trash; failures are told."""
        done = _Done()
        if wanted.serial is not None and not await self._hand_over(wanted.serial, product, done):
            done.next = _deletions(wanted.originals)
            return done
        for index, original in enumerate(wanted.originals):
            try:
                await self._removal.trash(original.id)
            except PaperlessMcpError as error:
                left = wanted.originals[index:]
                names = _named([d.id for d in left])
                done.notes.append(f"Not moved to the trash: {names} ({_cause(error)}).")
                done.next = _deletions(left)
                break
            done.trashed.append(original.id)
        if done.trashed:
            each = "It" if len(done.trashed) == 1 else "Each"
            done.notes.insert(0, f"Now in the trash: {_named(done.trashed)}. {each} {WAY_BACK}")
        return done

    async def _hand_over(
        self, original: DocumentRecord, product: DocumentRecord, done: _Done
    ) -> bool:
        """Give the original's serial number to the new document, or leave it where it was."""
        number = original.archive_serial_number
        if number is None:
            return True
        if product.archive_serial_number is not None:
            done.notes.append(
                HAS_ONE.format(
                    product=product.id,
                    held=product.archive_serial_number,
                    original=original.id,
                    number=number,
                )
            )
            return True
        try:
            _ = await self._number(original.id, None)
            _ = await self._number(product.id, number)
        except PaperlessMcpError as error:
            reason = NOT_MOVED.format(number=number, product=product.id, cause=_cause(error))
            done.notes.append(KEPT.format(reason=reason))
            try:
                _ = await self._number(original.id, number)
            except PaperlessMcpError:
                done.notes.append(
                    NOT_BACK.format(number=number, original=original.id, product=product.id)
                )
            return False
        return True

    async def _number(self, document_id: int, number: int | None) -> DocumentRecord:
        patch = DocumentPatch(archive_serial_number=number)
        return await self._writer.update_document(document_id, patch)


def _noted(result: PdfResult, notes: Sequence[str], *, next_call: str | None) -> PdfResult:
    message = " ".join([*([result.message] if result.message else []), *notes]) or None
    return result.model_copy(update={"message": message, "next": next_call})
