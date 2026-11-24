"""Looking through the Paperless trash, whose API has only pages and an order by a date.

Pages of that order can overlap or skip entries (seen on PostgreSQL, decision-47), so entries are
looked for in one bounded request first; pages follow only for ids that were not in it.
"""

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    NotFound,
    PaperlessMcpError,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.services.documents import TRASH_BYTES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.ports import DocumentReader, TrashReader
    from mcp_server_paperless.domain.trash import TrashedDocument, TrashFilter
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# Most entries a filter looks at, in one request (the number ``all=true`` handles, too).
MAX_SCAN: Final = 1000
# Entries per request while the trash is paged through, and when one request is too large.
PAGE: Final = 100
# shortcut: the trash is paged through for 100,000 entries, so that an answer that always names a
# next page ends; raise it once a trash is larger.
MAX_PAGES: Final = 1000
# Missing ids whose reason is looked up (two requests each); the rest is only named.
EXPLAINED: Final = 10
UNSEEN: Final = (
    "more may match. list_trash without a filter pages through the trash, and ids also looks "
    "beyond them."
)
SKIPPED: Final = "The trash could not be read at once, and its pages can skip entries."
OWNER_ONLY: Final = (
    "but was not found in the list: that may be because it is listed for its owner only."
)
NOT_LISTED_HINT: Final = (
    "List the trash with list_trash. Entries of documents the user does not own are not "
    "listed, and a version is listed only while its document is not in the trash."
)


def all_too_large() -> AnswerTooLarge:
    """The error for a trash of which not even a few entries can be read at once."""
    message = "The entries of the trash are too large to be looked through at once."
    hint = "List them page by page with list_trash, without a filter."
    return AnswerTooLarge(message, hint=hint)


def following(page: int, next_page: int | None) -> int | None:
    """The page to read after ``page``; None at the end, or if the answer does not get on."""
    return next_page if next_page is not None and page < next_page <= MAX_PAGES else None


@dataclass(frozen=True, slots=True)
class Scan:
    """What was read of the trash at once.

    Attributes:
        entries: The entries, each once, in Paperless order.
        total: How many entries the trash holds.
    """

    entries: tuple[TrashedDocument, ...]
    total: int

    @property
    def complete(self) -> bool:
        """Whether this is the whole trash."""
        return len(self.entries) >= self.total


@dataclass(frozen=True, slots=True)
class Selection:
    """The entries a filter matches, and what it could not look at.

    Attributes:
        missing: Ids of the filter that were not found in the trash.
    """

    entries: tuple[TrashedDocument, ...]
    scan: Scan
    missing: tuple[int, ...] = ()
    by_id: bool = False

    @property
    def truncated(self) -> bool:
        """Whether more entries may match than were looked at."""
        return not self.scan.complete and (bool(self.missing) or not self.by_id)

    @property
    def notice(self) -> str | None:
        """What was not looked at, in words."""
        parts: list[str] = []
        if self.missing:
            parts.append(f"Not found in the trash: {', '.join(map(str, self.missing))}.")
        if self.truncated and self.by_id:
            parts.append(SKIPPED)
        elif self.truncated:
            seen, total = len(self.scan.entries), self.scan.total
            parts.append(f"Only {seen} of the {total} entries were looked at; {UNSEEN}")
        return " ".join(parts) or None


class TrashScan:
    """Finds entries of the trash for the current user."""

    def __init__(
        self,
        reader: TrashReader,
        documents: DocumentReader,
        taxonomy: TaxonomyResolver,
        *,
        cap: int = MAX_SCAN,
    ) -> None:
        """Read through ``reader``, at most ``cap`` entries at once."""
        self._reader: TrashReader = reader
        self._documents: DocumentReader = documents
        self._taxonomy: TaxonomyResolver = taxonomy
        self.cap: int = cap

    async def first(self) -> Scan:
        """The first ``cap`` entries in one request, or fewer if that does not work.

        Raises:
            AnswerTooLarge: Even few entries are too large.
            UpstreamUnavailable: Even few entries could not be read.
        """
        try:
            page = await self._reader.read_trash(entries=self.cap, max_bytes=TRASH_BYTES)
        except AnswerTooLarge, UpstreamUnavailable:
            try:
                page = await self._reader.read_trash(
                    entries=min(PAGE, self.cap), max_bytes=TRASH_BYTES
                )
            except AnswerTooLarge:
                raise all_too_large() from None
        return Scan(tuple({e.id: e for e in page.entries}.values()), page.count)

    async def locate(self, ids: Sequence[int]) -> tuple[dict[int, TrashedDocument], Scan]:
        """Those of ``ids`` that are in the trash, reading on until all are found."""
        wanted = set(ids)
        try:
            scan = await self.first()
        except AnswerTooLarge, AnswerCompressed, UpstreamUnavailable:
            scan = Scan((), 1)  # nothing is known of it: pages follow
        found = {e.id: e for e in scan.entries if e.id in wanted}
        page: int | None = None if scan.complete else 1
        while page is not None and wanted - found.keys():
            listed = await self._reader.list_trash(page=page, page_size=PAGE)
            found |= {e.id: e for e in listed.entries if e.id in wanted}
            page = following(page, listed.next_page) if listed.entries else None
        return found, scan

    async def require(self, ids: Sequence[int]) -> list[TrashedDocument]:
        """The entries with these ids, in that order.

        Raises:
            NotFound: Some ids are not in the trash, or not visible to the user; each is named.
        """
        found, _ = await self.locate(ids)
        missing = [number for number in ids if number not in found]
        if missing:
            raise await self.unlisted(missing)
        entries = [found[number] for number in ids]
        with contextlib.suppress(PaperlessMcpError):  # only titles: acting does not need them
            return await self.rooted(entries)
        return entries

    async def unlisted(self, missing: Sequence[int]) -> NotFound:
        """The error for ids that are not listed, with the reason where Paperless tells one."""
        message = f"Not found in the trash: {', '.join(map(str, missing))}."
        reasons = [await self._reason(number) for number in missing[:EXPLAINED]]
        return NotFound(message, hint=" ".join([r for r in reasons if r] + [NOT_LISTED_HINT]))

    async def select(self, wanted: TrashFilter) -> Selection:
        """The entries that match ``wanted``, in Paperless order.

        Raises:
            Validation: The days contradict each other.
            NotFound: No such document type.
            Ambiguous: Several document types have the name.
        """
        first, last = wanted.deleted_from, wanted.deleted_to
        if first and last and first > last:
            message = "deleted_from is after deleted_to."
            raise Validation(message, hint="Swap them, or leave one out for an open range.")
        kind = wanted.document_type
        type_id = None if kind is None else (await self._taxonomy.resolve("document_type", kind)).id
        missing: list[int] = []
        if wanted.ids is None:
            scan = await self.first()
            entries = list(scan.entries)
        else:
            ids = list(dict.fromkeys(wanted.ids))
            found, scan = await self.locate(ids)
            entries = [found[number] for number in ids if number in found]
            missing = [number for number in ids if number not in found]
        entries = await self.rooted(entries)
        title = (wanted.title or "").casefold()
        matching = [
            e
            for e in entries
            if title in e.title.casefold()
            and type_id in {None, e.document_type}
            and wanted.kind in {None, "document" if e.version_of is None else "version"}
            and (first is None or e.deleted_on >= first)
            and (last is None or e.deleted_on <= last)
        ]
        return Selection(tuple(matching), scan, tuple(missing), by_id=wanted.ids is not None)

    async def rooted(self, entries: Sequence[TrashedDocument]) -> list[TrashedDocument]:
        """The entries; versions with the title and document type their document has now.

        Their own are those of the upload, without a type.
        """
        roots = list(dict.fromkeys(e.version_of for e in entries if e.version_of is not None))
        live: dict[int | None, dict[str, object]] = {}
        for start in range(0, len(roots), PAGE):
            page = await self._documents.search_documents(
                DocumentQuery(ids=tuple(roots[start : start + PAGE])), page=1, page_size=PAGE
            )
            live |= {
                hit.id: {"title": hit.title, "document_type": hit.document_type}
                for hit in page.hits
            }
        return [e.model_copy(update=live.get(e.version_of, {})) for e in entries]

    async def _reason(self, number: int) -> str | None:
        """Why an id is not listed, if Paperless lets this user know."""
        try:
            root = await self._documents.get_root(number)
        except PaperlessMcpError:
            return None
        try:
            document = await self._documents.get_document(root)
        except NotFound:
            if root != number:
                return (
                    f"{number} is a version of document {root}, which is in the trash itself: "
                    f"use {root}."
                )
            return f"{number} is in the trash, {OWNER_ONLY}"
        except PaperlessMcpError:
            return None
        if root == number or number in {v.id for v in document.versions}:
            return f"{number} is not deleted."
        return f"{number} is a deleted version of document {root}, {OWNER_ONLY}"
