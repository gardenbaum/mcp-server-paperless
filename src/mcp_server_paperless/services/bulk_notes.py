"""Notes on many documents in one call: one text for a selection, or a text per document.

A document that already has a note with the text gets none (decision-73), so repeating a call adds
nothing twice and a filter converges. That needs the notes: they are read at first, and those of
each document again right before its note is added. A POST whose answer was lost is never repeated;
the notes are read once and tell what became of it.

Paperless stores a note without the whitespace around it, so that is how texts are compared.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.bulk import MAX_DOCUMENTS
from mcp_server_paperless.domain.bulk_items import NotedDocument, NotesReport, NoteStatus
from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    AnswerTooLarge,
    ConfirmationRequired,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.selection import Selection
from mcp_server_paperless.services.bulk_documents import (
    DRY_RUN,
    NEXT_CALL,
    TO_GO_AHEAD,
    TO_GO_AHEAD_BY_IDS,
)
from mcp_server_paperless.services.bulk_operations import named, require_all
from mcp_server_paperless.services.bulk_updates import SHOWN
from mcp_server_paperless.services.document_runner import (
    MAX_WHY,
    RUN_SECONDS,
    Ended,
    cut,
    one_by_one,
    reasons,
    refused,
    stopped_by,
)
from mcp_server_paperless.services.documents import document_link
from mcp_server_paperless.services.notes import check_note
from mcp_server_paperless.services.read_documents import require_notes
from mcp_server_paperless.services.selection import SELECT_BYTES, require_exclusions, unchecked

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mcp_server_paperless.domain.bulk_items import DocumentNote
    from mcp_server_paperless.domain.documents import Note
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.domain.selection import DocumentRow, ReadField
    from mcp_server_paperless.services.selection import DocumentSelector, Selected

FIELDS: Final[tuple[ReadField, ...]] = ("title", "notes", "user_can_change")
# Characters of a note's start in a confirmation (decision-44); the whole text is bound.
START: Final = 80
WHICH: Final = "Pass either note with document_ids, filters or saved_view, or notes alone."
NO_VIEW: Final = (
    "Whether a note is already there cannot be told without it; add_note adds one note."
)
TWICE: Final = "a note that is there is not added twice."
AGAIN: Final = f"Pass these documents again once the reason is gone; {TWICE}"
GO_ON: Final = f"Pass the documents in left again; {TWICE}"


@dataclass(frozen=True, slots=True)
class _Item:
    """A note for a document, as it was read."""

    id: int
    title: str
    text: str
    may_change: bool
    there: int | None  # the note with this text


@dataclass(frozen=True, slots=True)
class _Call:
    """What a call is about: the notes to add, what is there, and what matches besides."""

    items: list[_Item]
    matched: int
    single: bool = True
    chosen: Selected | None = None
    alone: bool = False
    beside: bool = False
    rest: tuple[int, ...] = ()
    # How many documents that a filter alone matches have the note already.
    had: int = 0

    @property
    def adding(self) -> list[_Item]:
        return [item for item in self.items if item.there is None]

    @property
    def blocked(self) -> list[_Item]:
        return [item for item in self.adding if not item.may_change]

    @property
    def effect(self) -> str:
        """What confirming would do: every document, and the text or how it starts."""
        adding = self.adding
        if self.single:
            text = adding[0].text
            return (
                f"add the note {quoted(text, START)} ({len(text)} characters) to "
                f"{_named(adding)}{unchecked(self.chosen) if self.chosen else ''}"
            )
        told = [
            f"to {item.id} {quoted(item.title)} "
            + (quoted(item.text, START) if at < SHOWN else f"({len(item.text)} characters)")
            for at, item in enumerate(adding)
        ]
        return f"add {_notes(len(adding))}: {', '.join(told)}"

    def more(self, *, dry_run: bool) -> str:
        """What also matches and is not touched, as a sentence."""
        count = len(self.rest)
        if not count:
            return ""
        noun = "document matches" if count == 1 else "documents match"
        if self.alone:
            return f" {count} more {noun}: call again with the same filters or saved_view to go on."
        verb = "is" if dry_run else "was"
        verb = verb if count == 1 else {"is": "are", "was": "were"}[verb]
        return f" {count} other {noun} and {verb} not touched (also_matching)."


def _named(items: Sequence[_Item]) -> str:
    return named(list(dict.fromkeys((item.id, item.title) for item in items)))


def _notes(count: int) -> str:
    return f"{count} note" if count == 1 else f"{count} notes"


def _with(notes: Sequence[Note], text: str) -> int | None:
    """The newest note with ``text``."""
    return max((note.id for note in notes if note.note.strip() == text), default=None)


def _item(row: DocumentRow, text: str) -> _Item:
    return _Item(
        id=row.id,
        title=row.title or "",
        text=text,
        may_change=row.user_can_change is not False,
        there=_with(row.notes or (), text),
    )


class BulkNotes:
    """Adds notes to many documents for the current user."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        selector: DocumentSelector,
        *,
        web_url: str,
        clock: Callable[[], float],
    ) -> None:
        """Add notes to what ``selector`` chooses; ``clock`` bounds the call."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._selector: DocumentSelector = selector
        self._web_url: str = web_url
        self._clock: Callable[[], float] = clock

    async def add(
        self,
        selection: Selection,
        note: str | None,
        notes: Sequence[DocumentNote],
        *,
        dry_run: bool,
        confirm: bool,
    ) -> NotesReport:
        """Preview (``dry_run``) or add ``note`` to the selection, or each of ``notes``.

        Nothing is added for a preview, without a confirmation, or where the text is already.
        ``confirm`` confirms only documents that are named by id.

        Raises:
            Validation: No or both kinds of notes, an empty, too long or repeated one, or the
                selection is refused (see ``DocumentSelector.select``).
            NotFound: A document, view or name is unknown or hidden from the user.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not view notes, or not change one of the documents.
            ConfirmationRequired: Notes would be added and that is not confirmed.
            PaperlessMcpError: No note is known to be added and at least one is missing.
        """
        until = self._clock() + RUN_SECONDS
        if note is not None and not notes:
            call = await self._chosen(selection, note)
        elif note is None and notes and selection == Selection():
            call = await self._given(notes)
        else:
            raise Validation(WHICH, hint="notes holds a document_id and a note per item.")
        adding, blocked = call.adding, call.blocked
        if dry_run:
            return self._preview(call)
        if blocked:
            message = (
                f"You may not change {_named(blocked)}, so notes cannot be added there; nothing "
                "was sent."
            )
            hint = "Leave those documents out, or ask their owner for change permission."
            raise PermissionDenied(message, hint=hint)
        if not adding:
            return self._report(call, {}, _nothing(call))
        if call.alone or not confirm:
            ids = ", ".join(str(item.id) for item in adding)
            hint = NEXT_CALL.format(ids=ids, what="note")
            ConfirmationRequired.require(
                call.effect,
                scope=[(item.id, item.text) for item in adding],
                hint=hint if call.alone else CONFIRM_HINT,
            )
        rows, stop = await one_by_one(adding, self._add, until=until, clock=self._clock)
        return self._done(call, rows, stop)

    async def _given(self, notes: Sequence[DocumentNote]) -> _Call:
        if len(notes) > MAX_DOCUMENTS:
            message = f"Pass between 1 and {MAX_DOCUMENTS} notes; got {len(notes)}."
            raise Validation(message, hint="Work in several calls.")
        for given in notes:
            check_note(given.note, f"document {given.document_id}: ")
        pairs = [(given.document_id, given.note.strip()) for given in notes]
        twice = sorted({number for number, text in pairs if pairs.count((number, text)) > 1})
        if twice:
            message = f"The same note is given twice for document {', '.join(map(str, twice))}."
            raise Validation(message, hint="Name each note of a document once.")
        rows = await self._rows(list(dict.fromkeys(number for number, _ in pairs)))
        items = [_item(rows[number], text) for number, text in pairs]
        return _Call(items, len(rows), single=False)

    async def _chosen(self, selection: Selection, note: str) -> _Call:
        check_note(note)
        text = note.strip()
        ids = dict.fromkeys(selection.document_ids)
        if len(ids) > MAX_DOCUMENTS:
            message = f"Select between 1 and {MAX_DOCUMENTS} documents per call."
            raise Validation(message, hint="Work in several calls, or use filters.")
        chosen = await self._selector.select(selection, change=True)
        require_all(chosen.missing)
        require_exclusions(chosen)
        by_filter = selection.filters is not None or selection.saved_view is not None
        rows = await self._rows([hit.id for hit in (*chosen.hits, *chosen.others)])
        items = [_item(rows[hit.id], text) for hit in chosen.hits]
        if not by_filter:
            return _Call(items, chosen.matched, chosen=chosen)
        if ids:
            others = [_item(rows[hit.id], text) for hit in chosen.others]
            rest = tuple(item.id for item in others if item.there is None)
            return _Call(items, chosen.matched, chosen=chosen, beside=True, rest=rest)
        lacking = [item for item in items if item.there is None]
        return _Call(
            lacking[:MAX_DOCUMENTS],
            chosen.matched,
            chosen=chosen,
            alone=True,
            rest=tuple(item.id for item in lacking[MAX_DOCUMENTS:]),
            had=len(items) - len(lacking),
        )

    async def _rows(self, ids: Sequence[int]) -> dict[int, DocumentRow]:
        """The documents with their notes, which the user must be allowed to view."""
        rows: dict[int, DocumentRow] = {}
        for start in range(0, len(ids), MAX_DOCUMENTS):
            part = ids[start : start + MAX_DOCUMENTS]
            try:
                found = await self._reader.read_rows(part, FIELDS, max_bytes=SELECT_BYTES)
            except AnswerTooLarge:
                message = "The notes of these documents are larger than 64 MiB."
                raise AnswerTooLarge(message, hint="Choose fewer documents per call.") from None
            rows.update((row.id, row) for row in found)
        require_all([number for number in ids if number not in rows])
        await require_notes(self._reader, ids, hint=NO_VIEW, gone="Choose the documents again.")
        return rows

    async def _add(self, item: _Item) -> Ended[NotedDocument]:
        """Add one note, unless it is there by now."""
        try:
            there = _with(await self._reader.list_document_notes(item.id), item.text)
        except PaperlessMcpError as error:
            row = self._row(item, "failed", why=error.message)
            return Ended(row, stopped_by(error), silent=not refused(error))
        if there is not None:
            return Ended(self._row(item, "already_there", there))
        try:
            added = await self._writer.add_note(item.id, item.text)
        except PaperlessMcpError as error:
            # Paperless also says that it could not save when only what comes after that failed.
            if refused(error) and not isinstance(error, Validation):
                return Ended(self._row(item, "failed", why=error.message), stopped_by(error))
            return await self._look_up(item, error)
        return Ended(self._row(item, "added", added.id))

    async def _look_up(self, item: _Item, error: PaperlessMcpError) -> Ended[NotedDocument]:
        """What became of a note whose answer was lost, read once."""
        silent = not refused(error)
        try:
            there = _with(await self._reader.list_document_notes(item.id), item.text)
        except PaperlessMcpError as second:
            why = f"{error.message} The notes could not be read again: {second.message}"
            return Ended(self._row(item, "unknown", why=why), stopped_by(second), silent=silent)
        why = f"{error.message} Read again: the note is"
        if there is not None:
            row = self._row(item, "added", there, why=f"{why} there.")
        elif silent:
            row = self._row(item, "unknown", why=f"{why} not there, but may still appear.")
        else:
            row = self._row(item, "failed", why=f"{why} not there.")
        return Ended(row, silent=silent)

    def _row(
        self, item: _Item, status: NoteStatus, note_id: int | None = None, *, why: str | None = None
    ) -> NotedDocument:
        return NotedDocument(
            document_id=item.id,
            title=item.title,
            status=status,
            note_id=note_id,
            why=None if why is None else cut(why),
            web_url=document_link(self._web_url, item.id),
        )

    def _preview(self, call: _Call) -> NotesReport:
        planned = {
            id(item): self._row(item, "would_add" if item.may_change else "blocked")
            for item in call.adding
        }
        if call.blocked:
            what = f"It would be refused as a whole: you may not change {_named(call.blocked)}."
        elif planned:
            ahead = TO_GO_AHEAD_BY_IDS if call.alone else TO_GO_AHEAD
            what = f"Confirming would {call.effect}{_already(call)}. {ahead}"
            what += call.more(dry_run=True)
        else:
            what = _nothing(call)
        return self._report(call, planned, f"{DRY_RUN} {what}", dry_run=True)

    def _done(self, call: _Call, rows: Sequence[NotedDocument], stop: str) -> NotesReport:
        ended = {id(item): row for item, row in zip(call.adding, rows, strict=False)}
        for item in call.adding[len(rows) :]:
            ended[id(item)] = self._row(item, "not_started")
        report = self._report(call, ended, "")
        added = report.counts.get("added", 0)
        bad = [row for row in report.documents if row.status not in {"added", "already_there"}]
        done = f"Added {_notes(added)}"
        if not bad:
            already = report.counts.get("already_there", 0)
            done += f"; {already} {'was' if already == 1 else 'were'} already there" * bool(already)
            message = f"{done}.{call.more(dry_run=False)}"
            return report.model_copy(update={"message": message})
        counts = ", ".join(
            f"{n} {status}"
            for status, n in report.counts.items()
            if status not in {"added", "already_there"}
        )
        verb = "is" if len(bad) == 1 else "are"
        lacking = (
            f"{len(bad)} of {_notes(len(report.documents))} {verb} not known to be added: {counts}."
        )
        why = [(row.document_id, row.status, row.why) for row in bad if row.why]
        details = [reasons(why, limit=MAX_WHY if added else len(why))] if why else []
        if stop:
            details.append(f"Stopped: {stop}")
        if not added and why:
            message = f"No note is known to be added; {' '.join([lacking, *details])}"
            raise PaperlessMcpError(message, hint=f"Calling again is safe: {TWICE}")
        message = " ".join([lacking, f"{done}.", *details, AGAIN if why else GO_ON])
        return report.model_copy(update={"message": message + call.more(dry_run=False)})

    def _report(
        self, call: _Call, ended: dict[int, NotedDocument], message: str, *, dry_run: bool = False
    ) -> NotesReport:
        documents = tuple(
            ended.get(id(item)) or self._row(item, "already_there", item.there)
            for item in call.items
        )
        counts: dict[NoteStatus, int] = {}
        for row in documents:
            counts[row.status] = counts.get(row.status, 0) + 1
        if call.had:
            counts["already_there"] = counts.get("already_there", 0) + call.had
        left = dict.fromkeys(
            row.document_id for row in documents if row.status not in {"added", "already_there"}
        )
        return NotesReport(
            dry_run=dry_run,
            matched=call.matched,
            selected_ids=tuple(dict.fromkeys(item.id for item in call.adding)),
            counts=counts,
            documents=documents,
            remaining=len(call.rest) + (0 if dry_run else len(left)),
            also_matching=call.rest if call.beside else (),
            left=tuple(left),
            message=message,
        )


def _already(call: _Call) -> str:
    count = len(call.items) - len(call.adding) + call.had
    verb = "was" if count == 1 else "were"
    return f"; {count} {verb} already there" if count else ""


def _nothing(call: _Call) -> str:
    if not call.matched:
        return "Nothing to add: no document matches."
    return f"Nothing to add: every note is already there.{call.more(dry_run=False)}"
