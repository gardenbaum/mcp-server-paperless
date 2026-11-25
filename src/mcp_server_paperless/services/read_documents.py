"""Reading many documents in one call: chosen fields, text only on request (decision-70).

The text is not read from the list, which would send the whole text of every row, but document by
document, until the call has enough. What does not fit into the answer is named, never dropped.
"""

import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    AuthenticationFailed,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
)
from mcp_server_paperless.domain.files import joined
from mcp_server_paperless.domain.selection import (
    MAX_ANSWER,
    MAX_TEXT,
    Counted,
    CountedKind,
    DocumentRow,
    DocumentsRead,
    KindCounts,
    ReadField,
    ReadRow,
    SelectionCounts,
)
from mcp_server_paperless.services.documents import (
    document_link,
    known_items,
    maybe_ref,
    readable_fields,
    ref,
)
from mcp_server_paperless.services.selection import SELECT_BYTES
from mcp_server_paperless.services.trash_scan import limited

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from mcp_server_paperless.domain.documents import Ordering
    from mcp_server_paperless.domain.ports import DocumentReader
    from mcp_server_paperless.domain.selection import Selection
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.services.selection import DocumentSelector
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

DEFAULT_FIELDS: Final[tuple[ReadField, ...]] = (
    "title",
    "created",
    "correspondent",
    "document_type",
    "tags",
)
# No text is started this many seconds after the call began (decision-50's number).
TEXT_SECONDS: Final = 40.0
KINDS: Final[Mapping[ReadField, TaxonomyKind]] = {
    "correspondent": "correspondent",
    "document_type": "document_type",
    "storage_path": "storage_path",
    "tags": "tag",
    "custom_fields": "custom_field",
    "owner": "user",
}
# The field of SelectionCounts for each kind.
COUNTED: Final[Mapping[CountedKind, str]] = {
    "tag": "tags",
    "correspondent": "correspondents",
    "document_type": "document_types",
    "storage_path": "storage_paths",
    "custom_field": "custom_fields",
}
# Characters of the counts of one kind: the five together leave half of an answer to the rows.
COUNT_CHARS: Final = 10_000
NOT_COUNTED: Final = (
    f"The counts were left out: {TEXT_SECONDS:g} seconds had passed. Try again, or choose less."
)
# Characters of notices in an answer.
NOTICE_CHARS: Final = 5000
# The fields of a row whose size nothing bounds, and the text (as JSON up to six times as long).
GROWING: Final[tuple[ReadField, ...]] = ("notes", "custom_fields", "tags", "content")

NO_NOTES: Final = "Leave notes out of fields."
GONE: Final = "Choose the documents again, or leave notes out of fields."

ENDING: Final = (AuthenticationFailed, UpstreamUnreachable)
OUTAGES: Final = (
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
)


def _outage(error: PaperlessMcpError) -> bool:
    """Whether the server did not answer, rather than the document being broken."""
    return isinstance(error, UpstreamTimeout) or (
        isinstance(error, UpstreamUnavailable) and error.status in OUTAGES
    )


def _size(row: ReadRow) -> int:
    return len(row.model_dump_json()) + 1  # its comma


async def require_notes(
    reader: DocumentReader, ids: Sequence[int], *, hint: str, gone: str
) -> None:
    """Refuse a user who may not view notes: Paperless sends them in the rows anyway.

    The first document that answers decides; without any answer the notes are refused, too.
    """
    for number in ids:
        try:
            _ = await reader.list_document_notes(number)
        except NotFound:  # deleted meanwhile
            continue
        except PermissionDenied:
            message = (
                "The Paperless user may not view notes (it needs the permission to view notes)."
            )
            raise PermissionDenied(message, hint=hint) from None
        return
    if ids:
        message = "It could not be checked whether the user may view notes: the documents are gone."
        raise PaperlessMcpError(message, hint=gone)


class ReadDocuments:
    """Reads documents for the current user."""

    def __init__(
        self,
        reader: DocumentReader,
        selector: DocumentSelector,
        taxonomy: TaxonomyResolver,
        *,
        web_url: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Read what ``selector`` chooses through ``reader``; ``clock`` bounds reading texts."""
        self._reader: DocumentReader = reader
        self._selector: DocumentSelector = selector
        self._taxonomy: TaxonomyResolver = taxonomy
        self._web_url: str = web_url
        self._clock: Callable[[], float] = clock

    async def read(  # noqa: PLR0913 -- what is chosen, what is read of it, and the page
        self,
        selection: Selection,
        fields: Sequence[ReadField] | None,
        *,
        content_chars: int,
        counts: bool = False,
        ordering: Ordering | None,
        page: int,
        page_size: int,
    ) -> DocumentsRead:
        """One page of the documents of ``selection`` with ``fields`` (None: the usual ones).

        With ``counts`` also what all of them have, and no rows unless ``fields`` is given.

        Raises:
            Validation: The selection is refused (see ``DocumentSelector.select``).
            NotFound: A name matches nothing, or ``page`` is beyond the last page.
            Ambiguous: A name matches several.
            PermissionDenied: The user may not view documents, or the notes asked for.
            AnswerTooLarge: The rows Paperless sends are too large.
        """
        until = self._clock() + TEXT_SECONDS
        wanted = DEFAULT_FIELDS if fields is None else tuple(dict.fromkeys(fields))
        chosen = await self._selector.select(selection, ordering=ordering)
        every = tuple(hit.id for hit in chosen.hits)
        start = (page - 1) * page_size
        if page > 1 and start >= len(every):
            message = f"Page {page} is beyond the last page of the selection."
            hint = "Start with page=1; next_page tells how far it goes."
            raise NotFound(message, hint=hint)
        with_rows = fields is not None or not counts
        ids = every[start : start + page_size] if with_rows else ()
        if "notes" in wanted:
            await require_notes(self._reader, ids, hint=NO_NOTES, gone=GONE)
        notices = chosen.notices
        counted = await self._counts(every, until) if counts else None
        if counts and counted is None:
            notices = (NOT_COUNTED, *notices)
        rows = await self._rows(ids, wanted) if ids else []
        left: tuple[int, ...] = ()
        failed: tuple[int, ...] = ()
        if "content" in wanted:
            rows, failed, notice = await self._with_texts(rows, content_chars, until)
            notices = (*notice, *notices)
            left = tuple(row.id for row in rows if row.content is None and row.id not in failed)
        shown = {row.id for row in rows}
        answer = DocumentsRead(
            matched=chosen.matched,
            selected_ids=every if page == 1 else (),
            page=page,
            next_page=page + 1 if with_rows and start + page_size < len(every) else None,
            documents=(),
            missing=(*chosen.missing, *(number for number in ids if number not in shown)),
            also_matching=chosen.also_matching if page == 1 else (),
            left=left,
            failed=failed,
            counts=counted,
            notices=_short(notices),
        )
        return _bounded(answer, rows)

    async def _counts(self, ids: Sequence[int], until: float) -> SelectionCounts | None:
        """What the documents have; None when the time of the call is over.

        The names are read anew: from an older list a new object would count as hidden.
        """
        if not ids:
            return SelectionCounts()
        if self._clock() >= until:
            return None
        try:
            totals = await self._reader.count_selection(ids)
        except Validation, PermissionDenied:
            message = "The documents of the selection changed while they were counted."
            raise PaperlessMcpError(message, hint="Try again.") from None
        counts: dict[str, KindCounts] = {}
        for kind, name in COUNTED.items():
            if self._clock() >= until:
                return None
            self._taxonomy.invalidate(kind)
            counts[name] = _named(totals[kind], await known_items(self._taxonomy, kind))
        return SelectionCounts.model_validate(counts)

    async def _rows(self, ids: Sequence[int], fields: Sequence[ReadField]) -> list[ReadRow]:
        asked: list[ReadField] = [name for name in fields if name != "content"]
        try:
            found = await self._reader.read_rows(ids, asked, max_bytes=SELECT_BYTES)
        except AnswerTooLarge:
            message = "The documents of this page are larger than 64 MiB."
            hint = "Ask for a smaller page_size, or leave out notes or custom_fields."
            raise AnswerTooLarge(message, hint=hint) from None
        known: dict[TaxonomyKind, dict[int, TaxonomyItem]] = {
            kind: await known_items(self._taxonomy, kind)
            for name, kind in KINDS.items()
            if name in fields
        }
        by_id = {row.id: row for row in found}
        return [self._row(by_id[number], known) for number in ids if number in by_id]

    def _row(self, row: DocumentRow, known: dict[TaxonomyKind, dict[int, TaxonomyItem]]) -> ReadRow:
        return ReadRow(
            id=row.id,
            web_url=document_link(self._web_url, row.id),
            title=row.title,
            created=row.created,
            added=row.added,
            modified=row.modified,
            correspondent=maybe_ref(known, "correspondent", row.correspondent),
            document_type=maybe_ref(known, "document_type", row.document_type),
            storage_path=maybe_ref(known, "storage_path", row.storage_path),
            tags=None if row.tags is None else tuple(ref(known, "tag", t) for t in row.tags),
            custom_fields=(
                None if row.custom_fields is None else readable_fields(known, row.custom_fields)
            ),
            archive_serial_number=row.archive_serial_number,
            owner=maybe_ref(known, "user", row.owner),
            original_file_name=row.original_file_name,
            mime_type=row.mime_type,
            page_count=row.page_count,
            notes=row.notes,
            version_count=row.version_count,
            user_can_change=row.user_can_change,
        )

    async def _with_texts(
        self, rows: Sequence[ReadRow], chars: int, until: float
    ) -> tuple[list[ReadRow], tuple[int, ...], tuple[str, ...]]:
        """The rows with the start of their text as far as the call allows, what failed, and why.

        What concerns one text leaves the others. What concerns every request ends them: a 429
        (after the client's own retries), a rejected credential, no connection, and two outages
        in a row: the first stays failed, or a text that always times out would begin every call.
        """
        room = MAX_TEXT
        done = list(rows)
        failed: dict[str, list[int]] = {}
        outage = False  # whether the document before was one
        end: tuple[str, ...] = ()
        for at, row in enumerate(rows):
            if not room:
                break
            if self._clock() >= until:
                end = (f"No more text was read after {TEXT_SECONDS:g} seconds.",)
                break
            try:
                text = (await self._reader.get_document_content(row.id)).content
            except NotFound:  # deleted meanwhile: missing in the next call
                outage = False
                continue
            except PaperlessMcpError as error:
                again = _outage(error)
                if (again and outage) or limited(error) or isinstance(error, ENDING):
                    end = (f"Reading text stopped: {error.message}",)
                    break
                failed.setdefault(error.message, []).append(row.id)
                outage = again
                continue
            outage = False
            part = text[: min(chars, room)]
            room -= len(part)
            done[at] = row.model_copy(
                update={
                    "content": part,
                    "content_total": len(text),
                    "content_next": len(part) if len(part) < len(text) else None,
                }
            )
        told = [
            f"The text of {joined(ids)} could not be read in this call: {why}"
            for why, ids in failed.items()
            if ids
        ]
        ids = tuple(number for group in failed.values() for number in group)
        return done, ids, (*told, *end)


def _named(totals: Mapping[int, int], known: Mapping[int, TaxonomyItem]) -> KindCounts:
    """The objects with their names, as many as have room; hidden ones are only a number."""
    seen = [
        Counted(id=number, name=known[number].name, documents=count)
        for number, count in totals.items()
        if number in known
    ]
    seen.sort(key=lambda item: (-item.documents, item.name, item.id))
    room = COUNT_CHARS
    items: list[Counted] = []
    for item in seen:
        room -= len(item.model_dump_json()) + 1  # its comma
        if room < 0:
            break
        items.append(item)
    return KindCounts(
        items=tuple(items), more=len(seen) - len(items), hidden=len(totals) - len(seen)
    )


def _short(notices: Sequence[str]) -> tuple[str, ...]:
    """As many notices as may be told; the last one is cut. A hostile query has thousands."""
    room = NOTICE_CHARS
    told: list[str] = []
    for notice in notices:
        if len(notice) > room:
            return (*told, f"{notice[: room - 1]}…") if room > 0 else tuple(told)
        told.append(notice)
        room -= len(notice)
    return tuple(told)


def _bounded(answer: DocumentsRead, rows: Sequence[ReadRow]) -> DocumentsRead:
    """``answer`` with as many whole rows as fit; the first that does not fit ends them."""
    # As if every row were left out: what is named instead of a row is smaller than a row.
    empty = answer.model_copy(update={"left": tuple(row.id for row in rows)})
    alone = room = MAX_ANSWER - len(empty.model_dump_json())
    shown: list[ReadRow] = []
    for row in rows:
        fitted = _fitted(row, alone)
        room -= _size(fitted)
        if room < 0:
            break
        shown.append(fitted)
    cut = {row.id for row in rows[len(shown) :]}
    left = tuple(row.id for row in rows if row.id in cut or row.id in answer.left)
    return answer.model_copy(
        update={
            "documents": tuple(shown),
            "truncated": bool(cut),
            "left": left,
            "failed": tuple(number for number in answer.failed if number not in cut),
        }
    )


def _fitted(row: ReadRow, room: int) -> ReadRow:
    """``row``, without its largest fields if it could not be shown even alone."""
    sizes: dict[ReadField, int] = {
        name: len(row.model_dump_json(include={name}))
        for name in GROWING
        if getattr(row, name) is not None
    }
    dropped: tuple[ReadField, ...] = ()
    for largest in sorted(sizes, key=lambda name: sizes[name], reverse=True):
        if _size(row) <= room:
            break
        dropped = (*dropped, largest)
        update: dict[str, object] = {largest: None, "too_large": dropped}
        if largest == "content":
            update["content_next"] = 0
        row = row.model_copy(update=update)
    return row
