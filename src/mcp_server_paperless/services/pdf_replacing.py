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
    from mcp_server_paperless.domain.pdf import PdfResult, StartedTasks
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.services.removal import DocumentRemoval

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
    """

    originals: Sequence[DocumentRecord]
    pages: Mapping[int, int | None]
    serial: DocumentRecord | None = None
    problem: str | None = None
    unread: bool = False
    perhaps_none: bool = False


class _KeptError(Exception):
    """Why the originals are kept, and what to call to go on."""

    def __init__(self, reason: str, next_call: str | None = None) -> None:
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


def _named(numbers: Sequence[int]) -> str:
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
    return ", then ".join(steps) + staying


def _if_unchanged(left: Sequence[DocumentRecord]) -> str:
    names = _named([d.id for d in left])
    return f"only if {names} {'is' if len(left) == 1 else 'are'} unchanged"


def _if_found(original: DocumentRecord) -> str:
    return f"only if get_document(document_id={original.id}) still finds it unchanged"


def _unchecked(result: PdfResult, started: StartedTasks, wanted: Replacement) -> str:
    """What to call where tasks were not seen to their end: the conditions, then the rest."""
    waits = [f'get_task(task_id="{task.task_id}", wait_seconds=30)' for task in result.tasks]
    made = ", ".join(
        "a known number of pages" if pages is None else f"{pages} page{'' if pages == 1 else 's'}"
        for pages in (wanted.pages[n] for n in _outputs(result, started))
    )
    return (
        f"{', then '.join(waits)}; only if every one succeeded and made exactly one document "
        f"(get_document), with {made}, and {_if_unchanged(wanted.originals)}: "
        f"{_finish(wanted, wanted.originals, None, serial=True)}"
    )


def _outputs(result: PdfResult, started: StartedTasks) -> tuple[int, ...]:
    """The output file of each task."""
    return started.outputs or tuple(range(len(result.tasks)))


def _cause(error: PaperlessMcpError) -> str:
    return error.message.rstrip(".")


def _check_tasks(result: PdfResult, started: StartedTasks, wanted: Replacement) -> None:
    """Every task must be known to be this call's, waited for and successful."""
    if started.problem is not None or len(result.tasks) != len(wanted.pages):
        next_call: str | None = FIND
        if started.problem == "unconfirmed":
            next_call = f'get_task(task_id="{started.ids[0]}", wait_seconds=30)'
        elif wanted.perhaps_none and not result.tasks and started.problem is None:
            next_call = None
        reason = "what Paperless makes could not be followed"
        raise _KeptError(reason, next_call)
    if started.doubt is not None:
        reason = f"it cannot be told which files this call made ({started.doubt})"
        raise _KeptError(reason, FIND)
    if wanted.problem is not None:
        raise _KeptError(wanted.problem)
    if any(task.state in {"failure", "revoked"} for task in result.tasks):
        reason = "a task failed"
        raise _KeptError(reason)
    if result.wait == "not_requested":
        reason = "wait_seconds is 0, so nothing was checked"
        raise _KeptError(reason, _unchecked(result, started, wanted))
    if any(task.state in RUNNING for task in result.tasks):
        reason = "Paperless is not known to be done"
        raise _KeptError(reason, _unchecked(result, started, wanted))


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
            await self._may_start(wanted, product, began)
            for original in wanted.originals:
                await self._unchanged(original, original.archive_serial_number)
        except _KeptError as kept:
            notes = [KEPT.format(reason=kept.reason)]
            if result.produced_ids:
                notes.append(f"What was made stays: {_named(result.produced_ids)}.")
            return _noted(result, notes, next_call=kept.next_call)
        # Not to be cut short between taking a serial number from one document and giving it on.
        done = await asyncio.shield(asyncio.ensure_future(self._trash(wanted, product, began)))
        result = _noted(result, done.notes, next_call=done.next)
        return result.model_copy(update={"trashed_ids": tuple(done.trashed)})

    async def _product(
        self, result: PdfResult, started: StartedTasks, wanted: Replacement
    ) -> DocumentRecord:
        """The last of the new documents, all of which must be what they should be."""
        made: list[DocumentRecord] = []
        taken = {original.id for original in wanted.originals}
        for task, output in zip(result.tasks, _outputs(result, started), strict=True):
            if len(task.document_ids) != 1:
                found = _named(task.document_ids) if task.document_ids else "nothing"
                reason = f"task {task.task_id} made {found} instead of one document"
                raise _KeptError(reason)
            [number] = task.document_ids
            if number in taken:
                reason = f"document {number} is not a new document of its own"
                raise _KeptError(reason)
            taken.add(number)
            try:
                document = await self._reader.get_document(number)
            except PaperlessMcpError as error:
                reason = f"document {number} could not be read ({_cause(error)})"
                raise _KeptError(reason, f"get_document(document_id={number})") from None
            count, pages = document.page_count, wanted.pages[output]
            if count is None or pages not in {None, count}:
                has = "an unknown number of" if count is None else str(count)
                asked = "" if pages is None else f" instead of {pages}"
                reason = f"document {number} has {has} pages{asked}"
                raise _KeptError(reason)
            made.append(document)
        return made[-1]

    async def _may_start(self, wanted: Replacement, product: DocumentRecord, began: float) -> None:
        """Nothing starts without the rights being known, or later than ``LATEST_START``."""
        late = self.clock() - began > LATEST_START
        if wanted.unread or late:
            then = _finish(wanted, wanted.originals, product, serial=True)
            reason = UNREAD if wanted.unread else LATE
            raise _KeptError(reason, f"{_if_unchanged(wanted.originals)}: {then}")

    async def _unchanged(self, original: DocumentRecord, number: int | None) -> None:
        """The original must still be the one that was accepted, with this serial number."""
        try:
            now = await self._reader.get_document(original.id)
        except PaperlessMcpError as error:
            reason = f"document {original.id} could not be read again ({_cause(error)})"
            raise _KeptError(reason) from None
        if (now.newest_file, now.archive_serial_number) != (original.newest_file, number):
            reason = f"document {original.id} was changed meanwhile; look at it again"
            raise _KeptError(reason)

    async def _trash(self, wanted: Replacement, product: DocumentRecord, began: float) -> _Done:
        """Move the serial number, then the originals to the trash; failures are told."""
        done = _Done()
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
            each = "It" if len(done.trashed) == 1 else "Each"
            done.notes.insert(0, f"Now in the trash: {_named(done.trashed)}. {each} {WAY_BACK}")
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
            note = f"It is not known whether document {first.id} went to the trash ({why.cause}). "
            note += f"If it did, it {WAY_BACK}"
            if len(left) > 1:
                note += f" Not moved to the trash: {_named([d.id for d in left[1:]])}."
        else:
            note = f"Not moved to the trash: {_named([d.id for d in left])} ({why.cause})."
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
                done.next = f"{_if_unchanged(left)}: {then}"

    async def _move(
        self, original: DocumentRecord, number: int | None, began: float
    ) -> _Stayed | None:
        """Move one original to the trash after a last look; why it is not known to be there."""
        try:
            if self.clock() - began > LATEST_START:
                return _Stayed(LATE)
            await self._unchanged(original, number)
        except _KeptError as kept:
            return _Stayed(kept.reason)
        try:
            await self._removal.trash(original.id)
        except REFUSALS as error:
            return _Stayed(_cause(error))
        except PaperlessMcpError as error:
            try:
                _ = await self._reader.get_document(original.id)
            except NotFound:
                return None
            except PaperlessMcpError:
                pass
            # Still there says nothing: without an answer Paperless may delete it yet.
            return _Stayed(_cause(error), unknown=True)
        return None

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
            reason = NOT_MOVED.format(number=number, product=product.id, cause=_cause(error))
            done.notes.append(KEPT.format(reason=reason))
            place = await self._put_back(original, product)
            _tell_place(original, product, place, done)
            if place.holder > 0 and not place.taken:
                serial = place.holder == original.id
                then = _finish(wanted, wanted.originals, product, serial=serial)
                done.next = f"{_if_unchanged(wanted.originals)}: {then}"
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


def _noted(result: PdfResult, notes: Sequence[str], *, next_call: str | None) -> PdfResult:
    message = " ".join([*([result.message] if result.message else []), *notes]) or None
    return result.model_copy(update={"message": message, "next": next_call})
