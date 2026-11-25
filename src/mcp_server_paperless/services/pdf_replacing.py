"""The end of a PDF operation that replaces documents: verify, then trash (decision-60).

Paperless could trash the originals itself, but on the success of the tasks alone, and a task also
succeeds when it stops without a document. So this server does it, as the last step, and only if
every expected document exists, can be read and has the pages it should have, the originals are
still the ones that were accepted, and there is time left. Otherwise the originals stay, the result
says why, and ``next`` names the calls that finish the work. Nothing is remembered between calls.
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import DocumentPatch
from mcp_server_paperless.domain.errors import (
    AuthenticationFailed,
    Conflict,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.services.removal import WAY_BACK

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord
    from mcp_server_paperless.domain.pdf import PdfResult, PdfTask, StartedTasks
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.services.removal import DocumentRemoval
    from mcp_server_paperless.services.trash_scan import TrashScan

# No step of a composed call starts later than this after its beginning.
LATEST_START: Final = 45.0
LATE: Final = "the call took too long, though everything was made and checked"
UNREAD: Final = "the rights of this Paperless user could not be read"
KEPT: Final = "Nothing was moved to the trash: {reason}."
HAS_ONE: Final = (
    "Document {product} has the archive serial number {held} already, so document {original} keeps "
    "{number}."
)
NOT_MOVED: Final = (
    "archive serial number {number} could not be moved to document {product} ({cause})"
)
ELSEWHERE: Final = "Archive serial number {number} is on document {holder}."
TAKEN: Final = (
    "Archive serial number {number} is on document {holder}; document {original} has another one "
    "now."
)
LOST: Final = (
    "Archive serial number {number} is on neither document {original}, which has another one now, "
    "nor document {product}."
)
NOWHERE: Final = (
    "Archive serial number {number} is neither on document {original} nor on document {product}: "
    "set it with update_document(document_id={original}, archive_serial_number={number})."
)
UNKNOWN_PLACE: Final = (
    "It is not known where archive serial number {number} is: look at documents {original} and "
    "{product}."
)
# How long the look into the trash may take, which is what the call can be late by.
TRASH_SECONDS: Final = 10.0
NOT_IN_TRASH: Final = "it is not in the trash this user may see"
NOT_IN_TIME: Final = "the trash could not be read in time"
TRASH_UNREAD: Final = "the trash could not be read ({cause})"
UNEXPECTED: Final = "unexpected error"
FIND: Final = "find the new documents with search_documents (newest added first) and check them"
# Answers that say that nothing was deleted; any other error leaves that open.
REFUSALS: Final = (AuthenticationFailed, Conflict, PermissionDenied, Validation)
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
        unread: Whether the rights of the user could not be read when the plan was made: the user
            was told that the originals stay, so they do, whatever later reads say.
        perhaps_none: Whether a request without a task may simply have had nothing to do.
        unset: What metadata the new documents lack, if any.
        first: What comes before the last step in the next calls.
        then: The last step in the next calls, if it is not the deletion of the originals.
        kept: How a result says that the last step was not taken.
    """

    originals: Sequence[DocumentRecord]
    pages: Mapping[int, int | None]
    serial: DocumentRecord | None = None
    problem: str | None = None
    unread: bool = False
    perhaps_none: bool = False
    unset: str | None = None
    first: str = ""
    then: str = ""
    kept: str = KEPT


@dataclass(slots=True)
class Progress:
    """How far a call is that Paperless accepted, for when something unexpected stops it.

    Attributes:
        result: What is known so far.
        last: Whether the step that changes the original may have begun.
        product: The new document whose pages are being removed from the original, if any.
        trashed: The originals that are in the trash.
    """

    result: PdfResult
    last: bool = False
    product: int | None = None
    trashed: list[int] = field(default_factory=list[int])


class KeptError(Exception):
    """Why the last step is not taken, and what to call to go on."""

    def __init__(self, reason: str, next_call: str | None = None, *, unread: bool = False) -> None:
        """Create it from a clause for the message; ``unread``: a document could not be read."""
        self.unread: bool = unread
        super().__init__(reason)
        self.reason: str = reason
        self.next_call: str | None = next_call


@dataclass(frozen=True, slots=True)
class _Stayed:
    """Why an original is not known to be in the trash; ``unknown``: it may be all the same."""

    cause: str
    unknown: bool = False


@dataclass(frozen=True, slots=True)
class _Place:
    """Where a serial number is: ``holder`` is the document, 0 neither, -1 not known.

    ``taken``: its original has another number now, which is not to be overwritten.
    """

    holder: int
    taken: bool = False


@dataclass(slots=True)
class _Done:
    """What the last step did: the originals that went, and what is to be told."""

    trashed: list[int] = field(default_factory=list[int])
    notes: list[str] = field(default_factory=list[str])
    next: str | None = None
    unknown: bool = False


def named(numbers: Sequence[int]) -> str:
    """``document 3`` or ``documents 3, 5``."""
    noun = "document" if len(numbers) == 1 else "documents"
    return f"{noun} {', '.join(str(n) for n in numbers)}"


def _finish(
    wanted: Replacement,
    left: Sequence[DocumentRecord],
    product: DocumentRecord | None,
    *,
    serial: bool,
) -> str:
    """The calls that end the replacement of the originals that are ``left``.

    ``serial``: the number that is to move is still on its original. It never goes over one that
    the new document has; ``product`` is None where the new document is not known yet.
    """
    if wanted.then:
        return f"{wanted.first}{wanted.then}"
    steps = [f"delete_document(document_id={d.id})" for d in left]
    moves = wanted.serial if serial and wanted.serial in left else None
    if product is not None and product.archive_serial_number is not None:
        moves = None
    if moves is not None and moves.archive_serial_number is not None:
        new = "<the new document>" if product is None else str(product.id)
        number = moves.archive_serial_number
        move = (
            f'update_document(document_id={moves.id}, clear=["archive_serial_number"]), then '
            f"update_document(document_id={new}, archive_serial_number={number})"
        )
        if product is None:
            move = f"only if the new document has no archive serial number ({move})"
        steps = [move, *steps]
    staying = "".join(
        f"; archive serial number {d.archive_serial_number} of document {d.id} does not move"
        for d in left
        if d.archive_serial_number is not None
        and d is not moves
        and (serial or d is not wanted.serial)
    )
    return wanted.first + ", then ".join(steps) + staying


def if_unchanged(left: Sequence[DocumentRecord]) -> str:
    """The condition of a next call that these documents did not change."""
    names = named([d.id for d in left])
    return f"only if {names} {'is' if len(left) == 1 else 'are'} unchanged"


def _if_found(original: DocumentRecord) -> str:
    return f"only if get_document(document_id={original.id}) still finds it unchanged"


def _unchecked(result: PdfResult, started: StartedTasks, wanted: Replacement) -> str:
    """What to call where tasks were not seen to their end: the conditions, then the rest."""
    if any(task.task_id is None for task in result.tasks):
        return FIND
    waits = [f'get_task(task_id="{task.task_id}", wait_seconds=30)' for task in result.tasks]
    made = ", ".join(
        "a known number of pages" if pages is None else f"{pages} page{'' if pages == 1 else 's'}"
        for pages in (wanted.pages[n] for n in outputs(result, started))
    )
    return (
        f"{', then '.join(waits)}; only if every one succeeded and made exactly one document "
        f"(get_document), with {made}, and {if_unchanged(wanted.originals)}: "
        f"{_finish(wanted, wanted.originals, None, serial=True)}"
    )


def outputs(result: PdfResult, started: StartedTasks) -> tuple[int, ...]:
    """The output file of each task."""
    return started.outputs or tuple(range(len(result.tasks)))


def cause(error: Exception) -> str:
    """The message of an error as a clause; nothing of one that is not expected."""
    return error.message.rstrip(".") if isinstance(error, PaperlessMcpError) else UNEXPECTED


def _check_tasks(result: PdfResult, started: StartedTasks, wanted: Replacement) -> None:
    """Every task must be known to be this call's, waited for and successful."""
    if started.problem is not None or len(result.tasks) != len(wanted.pages):
        next_call: str | None = FIND
        if started.problem == "unconfirmed":
            next_call = f'get_task(task_id="{started.ids[0]}", wait_seconds=30)'
        elif wanted.perhaps_none and not result.tasks and started.problem is None:
            next_call = None
        reason = "what Paperless makes could not be followed"
        raise KeptError(reason, next_call)
    if started.doubt is not None:
        reason = f"it cannot be told which files this call made ({started.doubt})"
        raise KeptError(reason, FIND)
    if wanted.problem is not None:
        raise KeptError(wanted.problem)
    if any(task.state in {"failure", "revoked"} for task in result.tasks):
        reason = "a task failed"
        raise KeptError(reason)
    if result.wait == "not_requested":
        reason = "wait_seconds is 0, so nothing was checked"
        raise KeptError(reason, _unchecked(result, started, wanted))
    if any(task.state in RUNNING for task in result.tasks):
        reason = "Paperless is not known to be done"
        raise KeptError(reason, _unchecked(result, started, wanted))


class Replacing:
    """Verifies what an operation made and trashes the originals, for the current user."""

    def __init__(  # noqa: PLR0913 -- one port or service per concern
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        removal: DocumentRemoval,
        trash: TrashScan,
        *,
        clock: Callable[[], float] = time.monotonic,
        trash_seconds: float = TRASH_SECONDS,
    ) -> None:
        """Read through ``reader``, move serial numbers through ``writer``, trash with ``removal``.

        ``trash`` finds what is not found any more, for at most ``trash_seconds``. ``clock`` bounds
        how late the last step may start.
        """
        self._trash_scan: TrashScan = trash
        self._trash_seconds: float = trash_seconds
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._removal: DocumentRemoval = removal
        self.clock: Callable[[], float] = clock

    async def finish(
        self,
        result: PdfResult,
        started: StartedTasks,
        wanted: Replacement,
        *,
        began: float,
        progress: Progress,
    ) -> PdfResult:
        """``result`` after the last step: originals trashed, or why not and how to go on.

        ``progress`` is told when the last step begins.
        """
        single = len(wanted.originals) == 1
        try:
            product = await self.checked(result, started, wanted, began=began)
            for original in wanted.originals:
                await self.unchanged(original, original.archive_serial_number)
        except KeptError as kept:
            return stays(result, wanted, kept.reason, kept.next_call)
        progress.last = True
        # Not to be cut short between taking a serial number from one document and giving it on.
        done = await asyncio.shield(
            asyncio.ensure_future(self._trash(wanted, product, began, progress.trashed))
        )
        result = noted(result, done.notes, next_call=done.next)
        original = "trashed" if done.trashed else "unknown" if done.unknown else "kept"
        return result.model_copy(
            update={
                "trashed_ids": tuple(done.trashed),
                "original": original if single else None,
            }
        )

    async def checked(
        self, result: PdfResult, started: StartedTasks, wanted: Replacement, *, began: float
    ) -> DocumentRecord:
        """The last of the new documents, if everything is verified and there is time left.

        Raises:
            KeptError: It is not: the last step must not be taken.
        """
        _check_tasks(result, started, wanted)
        product = await self._product(result, started, wanted)
        if wanted.unset is not None:
            then = _finish(wanted, wanted.originals, product, serial=True)
            raise KeptError(wanted.unset, f"{if_unchanged(wanted.originals)}: {then}")
        await self._may_start(wanted, product, began)
        return product

    def late(self, began: float) -> bool:
        """Whether it is too late to start another step of a call that began then."""
        return self.clock() - began > LATEST_START

    async def verified(self, task: PdfTask, pages: int | None, taken: set[int]) -> DocumentRecord:
        """The one new document of a successful task, with its pages; adds it to ``taken``.

        Raises:
            KeptError: It is not what it should be.
        """
        if len(task.document_ids) != 1:
            found = named(task.document_ids) if task.document_ids else "nothing"
            reason = f"task {task.task_id} made {found} instead of one document"
            raise KeptError(reason)
        [number] = task.document_ids
        if number in taken:
            reason = f"document {number} is not a new document of its own"
            raise KeptError(reason)
        taken.add(number)
        try:
            document = await self._reader.get_document(number)
        except PaperlessMcpError as error:
            reason = f"document {number} could not be read ({cause(error)})"
            raise KeptError(reason, f"get_document(document_id={number})", unread=True) from None
        count = document.page_count
        if count is None or pages not in {None, count}:
            has = "an unknown number of" if count is None else str(count)
            asked = "" if pages is None else f" instead of {pages}"
            reason = f"document {number} has {has} pages{asked}"
            raise KeptError(reason)
        return document

    async def _product(
        self, result: PdfResult, started: StartedTasks, wanted: Replacement
    ) -> DocumentRecord:
        """The last of the new documents, all of which must be what they should be."""
        taken = {original.id for original in wanted.originals}
        made = [
            await self.verified(task, wanted.pages[output], taken)
            for task, output in zip(result.tasks, outputs(result, started), strict=True)
        ]
        return made[-1]

    async def _may_start(self, wanted: Replacement, product: DocumentRecord, began: float) -> None:
        """Nothing starts without the rights being known, or later than ``LATEST_START``."""
        late = self.late(began)
        if wanted.unread or late:
            then = _finish(wanted, wanted.originals, product, serial=True)
            reason = UNREAD if wanted.unread else LATE
            raise KeptError(reason, f"{if_unchanged(wanted.originals)}: {then}")

    async def unchanged(self, original: DocumentRecord, number: int | None) -> None:
        """The original must still be the one that was accepted, with this serial number."""
        try:
            now = await self._reader.get_document(original.id)
        except PaperlessMcpError as error:
            reason = f"document {original.id} could not be read again ({cause(error)})"
            raise KeptError(reason, unread=True) from None
        if (now.newest_file, now.archive_serial_number) != (original.newest_file, number):
            reason = f"document {original.id} was changed meanwhile; look at it again"
            raise KeptError(reason)

    async def _trash(
        self, wanted: Replacement, product: DocumentRecord, began: float, trashed: list[int]
    ) -> _Done:
        """Move the serial number, then the originals to the trash; failures are told."""
        done = _Done(trashed)
        moved = await self._hand_over(wanted, product, done)
        if moved is None:
            return done
        for index, original in enumerate(wanted.originals):
            gave = moved and original is wanted.serial
            why = await self._move(
                original, None if gave else original.archive_serial_number, began
            )
            if why is not None:
                await self._stopped(wanted, product, done, why, moved=moved, index=index)
                break
            done.trashed.append(original.id)
        if done.trashed:
            done.notes.insert(0, gone(done.trashed))
        return done

    async def _stopped(  # noqa: PLR0913 -- what is to be told
        self,
        wanted: Replacement,
        product: DocumentRecord,
        done: _Done,
        why: _Stayed,
        *,
        moved: bool,
        index: int,
    ) -> None:
        """Tell what stays, give the number back to an original that stays, name the next calls."""
        left = wanted.originals[index:]
        first = left[0]
        if why.unknown:
            done.unknown = True
            note = f"It is not known whether document {first.id} went to the trash ({why.cause}). "
            note += f"If it did, it {WAY_BACK}"
            if len(left) > 1:
                note += f" Not moved to the trash: {named([d.id for d in left[1:]])}."
        else:
            note = f"Not moved to the trash: {named([d.id for d in left])} ({why.cause})."
        done.notes.append(note)
        original = wanted.serial
        place = _Place(product.id) if moved or original is None else _Place(original.id)
        if moved and original is not None and original in left:
            if not (why.unknown and first is original):
                place = await self._put_back(original, product)
            _tell_place(original, product, place, done)
        if place.holder > 0 and not place.taken:
            serial = original is not None and place.holder == original.id
            if why.unknown:
                done.next = ". Whatever became of it, ".join(
                    f"{_if_found(d)}: {_finish(wanted, [d], product, serial=serial)}" for d in left
                )
            else:
                then = _finish(wanted, left, product, serial=serial)
                done.next = f"{if_unchanged(left)}: {then}"

    async def _move(
        self, original: DocumentRecord, number: int | None, began: float
    ) -> _Stayed | None:
        """Move one original to the trash after a last look; why it is not known to be there."""
        try:
            if self.late(began):
                return _Stayed(LATE)
            await self.unchanged(original, number)
        except KeptError as kept:
            return _Stayed(kept.reason)
        try:
            await self._removal.trash(original.id)
        except REFUSALS as error:
            return _Stayed(cause(error))
        except PaperlessMcpError as error:
            try:
                _ = await self._reader.get_document(original.id)
            except NotFound:
                # Trashed, or hidden from this user meanwhile: only the trash can tell.
                seen = await self._in_trash(original.id)
                return None if seen is None else _Stayed(f"{cause(error)}; {seen}", unknown=True)
            except PaperlessMcpError:
                pass
            # Still there says nothing: without an answer Paperless may delete it yet.
            return _Stayed(cause(error), unknown=True)
        return None

    async def _in_trash(self, document_id: int) -> str | None:
        """None if the trash holds the document, else why that is not known."""
        try:
            async with asyncio.timeout(self._trash_seconds):
                found, _ = await self._trash_scan.locate([document_id])
        except TimeoutError:
            return NOT_IN_TIME
        except PaperlessMcpError as error:
            return TRASH_UNREAD.format(cause=cause(error))
        return None if document_id in found else NOT_IN_TRASH

    async def _hand_over(
        self, wanted: Replacement, product: DocumentRecord, done: _Done
    ) -> bool | None:
        """Whether the original's serial number moved to the new document; None: it failed to."""
        original = wanted.serial
        if original is None or original.archive_serial_number is None:
            return False
        number = original.archive_serial_number
        if product.archive_serial_number is not None:
            held = product.archive_serial_number
            done.notes.append(
                HAS_ONE.format(product=product.id, held=held, original=original.id, number=number)
            )
            return False
        try:
            _ = await self._number(original.id, None)
            _ = await self._number(product.id, number)
        except PaperlessMcpError as error:
            reason = NOT_MOVED.format(number=number, product=product.id, cause=cause(error))
            done.notes.append(KEPT.format(reason=reason))
            place = await self._put_back(original, product)
            _tell_place(original, product, place, done)
            if place.holder > 0 and not place.taken:
                serial = place.holder == original.id
                then = _finish(wanted, wanted.originals, product, serial=serial)
                done.next = f"{if_unchanged(wanted.originals)}: {then}"
            return None
        return True

    async def _put_back(self, original: DocumentRecord, product: DocumentRecord) -> _Place:
        """Put the number back on its original unless that has another one; where it is then."""
        number = original.archive_serial_number
        try:
            place = await self._locate(original, product)
            if place.holder == original.id or place.taken:
                return place
            if place.holder == product.id:
                _ = await self._number(product.id, None)
            _ = await self._number(original.id, number)
        except PaperlessMcpError:
            with contextlib.suppress(PaperlessMcpError):
                return await self._locate(original, product)
            return _Place(-1)
        return _Place(original.id)

    async def _locate(self, original: DocumentRecord, product: DocumentRecord) -> _Place:
        number = original.archive_serial_number
        held = (await self._reader.get_document(original.id)).archive_serial_number
        if held == number:
            return _Place(original.id)
        there = (await self._reader.get_document(product.id)).archive_serial_number == number
        return _Place(product.id if there else 0, taken=held is not None)

    async def _number(self, document_id: int, number: int | None) -> DocumentRecord:
        patch = DocumentPatch(archive_serial_number=number)
        return await self._writer.update_document(document_id, patch)


def _tell_place(
    original: DocumentRecord, product: DocumentRecord, place: _Place, done: _Done
) -> None:
    """Say where the number is, unless it is on its original."""
    if place.holder == original.id:
        return
    text = TAKEN if place.taken else ELSEWHERE
    if place.holder == 0:
        text = LOST if place.taken else NOWHERE
    elif place.holder < 0:
        text = UNKNOWN_PLACE
    done.notes.append(
        text.format(
            number=original.archive_serial_number,
            original=original.id,
            product=product.id,
            holder=place.holder,
        )
    )


def gone(numbers: Sequence[int]) -> str:
    """What is in the trash, and the way back."""
    each = "It" if len(numbers) == 1 else "Each"
    return f"Now in the trash: {named(numbers)}. {each} {WAY_BACK}"


def stays(result: PdfResult, wanted: Replacement, reason: str, next_call: str | None) -> PdfResult:
    """``result``, saying why the last step was not taken."""
    notes = [wanted.kept.format(reason=reason)]
    if result.produced_ids:
        notes.append(f"What was made stays: {named(result.produced_ids)}.")
    original = "kept" if len(wanted.originals) == 1 else None
    return noted(result, notes, next_call=next_call).model_copy(update={"original": original})


def noted(result: PdfResult, notes: Sequence[str], *, next_call: str | None) -> PdfResult:
    """``result`` with more to its message, and its next calls."""
    message = " ".join([*([result.message] if result.message else []), *notes]) or None
    return result.model_copy(update={"message": message, "next": next_call})
