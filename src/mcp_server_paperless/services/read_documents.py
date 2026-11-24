"""Reading many documents in one call: chosen fields, text only on request (decision-70).

The text is not read from the list, which would send the whole text of every row, but document by
document, until the call has enough. What does not fit into the answer is named, never dropped.
"""

import time
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
)
from mcp_server_paperless.domain.selection import (
    MAX_ANSWER,
    MAX_TEXT,
    DocumentRow,
    DocumentsRead,
    ReadField,
    ReadRow,
)
from mcp_server_paperless.services.documents import (
    document_link,
    known_items,
    maybe_ref,
    readable_fields,
    ref,
)
from mcp_server_paperless.services.selection import SELECT_BYTES

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
# The fields of a row whose size nothing bounds, and the text (as JSON up to six times as long).
GROWING: Final[tuple[ReadField, ...]] = ("notes", "custom_fields", "content")


def _size(row: ReadRow) -> int:
    return len(row.model_dump_json()) + 1  # its comma


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
        ordering: Ordering | None,
        page: int,
        page_size: int,
    ) -> DocumentsRead:
        """One page of the documents of ``selection`` with ``fields`` (None: the usual ones).

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
        ids = every[start : start + page_size]
        if "notes" in wanted:
            await self._require_notes(ids)
        rows = await self._rows(ids, wanted)
        notices = chosen.notices
        left: tuple[int, ...] = ()
        if "content" in wanted:
            rows, notice = await self._with_texts(rows, content_chars, until)
            notices += notice
            left = tuple(row.id for row in rows if row.content is None)
        shown = {row.id for row in rows}
        answer = DocumentsRead(
            matched=chosen.matched,
            selected_ids=every if page == 1 else (),
            page=page,
            next_page=page + 1 if start + page_size < len(every) else None,
            documents=(),
            missing=(*chosen.missing, *(number for number in ids if number not in shown)),
            also_matching=chosen.also_matching,
            left=left,
            notices=notices,
        )
        return _bounded(answer, rows)

    async def _require_notes(self, ids: Sequence[int]) -> None:
        """Refuse a user who may not view notes: Paperless sends them in the rows anyway."""
        for number in ids:
            try:
                _ = await self._reader.list_document_notes(number)
            except NotFound:  # deleted meanwhile
                continue
            except PermissionDenied:
                message = (
                    "The Paperless user may not view notes (it needs the permission to view notes)."
                )
                raise PermissionDenied(message, hint="Leave notes out of fields.") from None
            return

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
    ) -> tuple[list[ReadRow], tuple[str, ...]]:
        """The rows with the first ``chars`` characters of their text, as far as the call allows."""
        room = MAX_TEXT
        done = list(rows)
        for at, row in enumerate(rows):
            if not room:
                break
            if self._clock() >= until:
                return done, (f"No more text was read after {TEXT_SECONDS:g} seconds.",)
            try:
                text = (await self._reader.get_document_content(row.id)).content
            except NotFound:  # deleted meanwhile
                continue
            except PaperlessMcpError as error:  # also 429: no other request follows
                return done, (f"Reading text stopped: {error.message}",)
            part = text[: min(chars, room)]
            room -= len(part)
            done[at] = row.model_copy(
                update={
                    "content": part,
                    "content_total": len(text),
                    "content_next": len(part) if len(part) < len(text) else None,
                }
            )
        return done, ()


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
        update={"documents": tuple(shown), "truncated": bool(cut), "left": left}
    )


def _fitted(row: ReadRow, room: int) -> ReadRow:
    """``row``, without its largest fields if it could not be shown even alone."""
    dropped: tuple[ReadField, ...] = ()
    while _size(row) > room:
        sizes: dict[ReadField, int] = {
            name: len(row.model_dump_json(include={name}))
            for name in GROWING
            if getattr(row, name) is not None
        }
        if not sizes:
            break  # not shown at all: the caller names it
        largest = max(sizes, key=lambda name: sizes[name])
        dropped = (*dropped, largest)
        update: dict[str, object] = {largest: None, "too_large": dropped}
        if largest == "content":
            update["content_next"] = 0
        row = row.model_copy(update=update)
    return row
