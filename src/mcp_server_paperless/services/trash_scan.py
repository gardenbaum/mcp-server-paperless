"""Looking through the Paperless trash, whose API has only pages and an order by a date.

Pages of that order can overlap or skip entries (seen on PostgreSQL, decision-47), so entries are
looked for in one bounded request first; pages follow only for ids that were not in it.
"""

import contextlib
from dataclasses import dataclass, replace
from http import HTTPStatus
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
    from collections.abc import AsyncGenerator, Sequence

    from mcp_server_paperless.domain.ports import DocumentReader, TrashReader
    from mcp_server_paperless.domain.trash import TrashedDocument, TrashFilter, TrashPage
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# Most entries a filter looks at, in one request (the number ``all=true`` handles, too).
MAX_SCAN: Final = 1000
# Entries per request while the trash is paged through, and when one request is too large.
PAGE: Final = 100
# How much smaller a page becomes that is too large: 100, 10 and 1 entries.
STEP: Final = 10
# shortcut: the trash is paged through in at most 1,000 requests (100,000 entries, fewer where the
# pages are small), so that a search always ends; raise it once a trash is larger.
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


OVER: Final = "An entry over the limit of 64 MiB was not read"
TOO_LARGE: Final = f"{OVER}, so it may be one of these: look in the Paperless web UI."
STOPPED: Final = f"The trash was looked through in {MAX_PAGES} requests and is longer."


def all_too_large() -> AnswerTooLarge:
    """The error for a trash of which not even a few entries can be read at once."""
    message = "The entries of the trash are too large to be looked through at once."
    hint = "List them page by page with list_trash, without a filter."
    return AnswerTooLarge(message, hint=hint)


def limited(error: PaperlessMcpError) -> bool:
    """Whether Paperless asks for fewer requests, so that no other one is made instead."""
    return isinstance(error, UpstreamUnavailable) and error.status == HTTPStatus.TOO_MANY_REQUESTS


def following(page: int, next_page: int | None) -> int | None:
    """The page to read after ``page``; None at the end, or if the answer does not get on."""
    return next_page if next_page is not None and page < next_page <= MAX_PAGES else None


@dataclass(frozen=True, slots=True)
class Scan:
    """What was read of the trash at once.

    Attributes:
        entries: The entries, each once, in Paperless order.
        total: How many entries the trash holds.
        small: The large request did not work, so it is not made again.
        unread: What could not be looked at in the pages, in words.
    """

    entries: tuple[TrashedDocument, ...]
    total: int
    small: bool = False
    unread: str | None = None

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
        if self.missing and self.scan.unread:
            parts.append(self.scan.unread)
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

    async def first(self, *, small: bool = False) -> Scan:
        """The first ``cap`` entries in one request, or fewer (``small``) if that does not work.

        Raises:
            AnswerTooLarge: Even few entries are too large.
            UpstreamUnavailable: Even few entries could not be read.
        """
        size = min(PAGE, self.cap) if small else self.cap
        try:
            page = await self._reader.read_trash(entries=size, max_bytes=TRASH_BYTES)
        except (AnswerTooLarge, UpstreamUnavailable) as error:
            if limited(error):
                raise
            if not small:
                return await self.first(small=True)
            raise (all_too_large() if isinstance(error, AnswerTooLarge) else error) from None
        return Scan(tuple({e.id: e for e in page.entries}.values()), page.count, small)

    async def locate(
        self, ids: Sequence[int], *, small: bool = False
    ) -> tuple[dict[int, TrashedDocument], Scan]:
        """Those of ``ids`` that are in the trash, reading on until all are found."""
        wanted = set(ids)
        try:
            scan = await self.first(small=small)
        except (AnswerTooLarge, AnswerCompressed, UpstreamUnavailable) as error:
            if limited(error):
                raise
            scan = Scan((), 1, small=True)  # nothing is known of it: pages follow
        found = {e.id: e for e in scan.entries if e.id in wanted}
        if scan.complete or wanted <= found.keys():
            return found, scan
        unread: list[str] = []
        async with contextlib.aclosing(self.pages()) as pages:
            async for page in pages:
                if isinstance(page, str):
                    unread.append(page)
                    continue
                found |= {e.id: e for e in page.entries if e.id in wanted}
                if wanted <= found.keys():
                    return found, scan
        return found, replace(scan, unread=" ".join(dict.fromkeys(unread)) or None)

    async def pages(self) -> AsyncGenerator[TrashPage | str]:
        """The trash in pages of ``PAGE`` entries, where they are too large in smaller ones.

        A text stands for what could not be read: an entry that is too large on its own, or the rest
        after ``MAX_PAGES`` requests.
        """
        start, size = 0, PAGE
        for _ in range(MAX_PAGES):
            number = start // size + 1
            try:
                page = await self._reader.list_trash(
                    page=number, page_size=size, max_bytes=TRASH_BYTES
                )
            except AnswerTooLarge:
                if size > 1:
                    size //= STEP
                    continue
                yield TOO_LARGE
            except NotFound:  # the entry before was the last one
                return
            else:
                yield page
                if not page.entries or page.next_page is None or page.next_page <= number:
                    return
            start += size
            while size < PAGE and start % (size * STEP) == 0:
                size *= STEP
        yield STOPPED

    async def require(self, ids: Sequence[int]) -> tuple[list[TrashedDocument], bool]:
        """The entries with these ids, in that order, and whether only small requests worked.

        Raises:
            NotFound: Some ids are not in the trash, or not visible to the user; each is named.
        """
        found, scan = await self.locate(ids)
        missing = [number for number in ids if number not in found]
        if missing:
            raise await self.unlisted(missing, scan)
        return await self.titled([found[number] for number in ids]), scan.small

    async def unlisted(self, missing: Sequence[int], scan: Scan) -> NotFound:
        """The error for ids that are not listed, with the reason where Paperless tells one."""
        message = f"Not found in the trash: {', '.join(map(str, missing))}."
        reasons = [await self._reason(number) for number in missing[:EXPLAINED]]
        gaps = [scan.unread, None if scan.complete else SKIPPED]
        return NotFound(message, hint=" ".join([*filter(None, [*reasons, *gaps]), NOT_LISTED_HINT]))

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
        by_document = wanted.title is not None or type_id is not None
        entries = await (self.rooted(entries) if by_document else self.titled(entries))
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

    async def titled(self, entries: Sequence[TrashedDocument]) -> list[TrashedDocument]:
        """The entries ``rooted``; as they are if their documents cannot be read."""
        try:
            return await self.rooted(entries)
        except PaperlessMcpError:
            return list(entries)

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
