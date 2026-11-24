"""Where Paperless already holds a file: the check around an upload (decision-47, decision-52).

Files are compared by their SHA-256, the checksum Paperless 3.5 stores. Before sending, the
original uploads of documents are found with a filter; versions and the trash have none, so they
are read through, the most recently changed documents first, up to ``Bounds``. After consumption
Paperless reports duplicates of documents itself. What was not compared is always said, and a
document the user may not see is never named. Two uploads of one file at the same time both pass
the check before sending; the report afterwards shows it.
"""

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.duplicates import (
    DuplicateCheck,
    DuplicateMatch,
    DuplicateReport,
)
from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    quoted,
)
from mcp_server_paperless.services.documents import document_link

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterable, Mapping

    from mcp_server_paperless.domain.documents import DocumentRecord
    from mcp_server_paperless.domain.ports import DocumentReader, TrashReader
    from mcp_server_paperless.domain.trash import TrashedDocument, TrashPage
    from mcp_server_paperless.domain.versions import VersionRow

logger = logging.getLogger(__name__)

UNCOMPARED: Final = (
    "Not compared before sending: archive files, and versions of documents in the trash."
)
ALTERED: Final = (
    "Not found: copies that Paperless altered before storing them (pre-consume scripts, split "
    "barcodes), and documents that still have the MD5 checksum of an older release."
)
OWN_TRASH: Final = (
    "Trash: documents of others that are shared with this user are not listed there (superusers "
    "see all)."
)
HIDDEN: Final = "Documents this user cannot see are never compared."
VERSIONS_AFTER: Final = "After consumption Paperless does not compare versions."
VERSION_AFTER: Final = (
    "Paperless reports no duplicates of versions: after consumption this server compared the "
    "originals of documents outside the trash and the other versions of this document only."
)
UNREAD_AFTER: Final = "After consumption the duplicates could not be read."
CAPPED: Final = (
    "Versions: only the {read} most recently changed of {total} documents were read; a document "
    "changed meanwhile may be missed."
)
TRASH_UNREAD: Final = (
    "Trash: neither its documents nor its deleted versions were checked, because its entries "
    "(the first {entries}) with the text of their documents are larger than {mib} MiB."
)
UNNAMED: Final = "{more} more documents with the same file are not named."
REJECTED_FOR: Final = "Paperless rejected the file: it already holds the same file, {where}."
HIDDEN_REJECTED: Final = REJECTED_FOR.format(
    where="in a document that this user cannot see or that no longer exists"
)
UNREAD_REJECTED: Final = REJECTED_FOR.format(where="but where could not be read")
# Matches a confirmation and a result name one by one; the rest are counted.
SHOWN: Final = 10
NO_SKIP: Final = "Neither confirm=true nor a setting skips the check."
AGAIN: Final = f"Try again in a moment. {NO_SKIP}"
MIB: Final = 1024 * 1024
RESTORABLE: Final = (
    "What is in the trash can be restored in the Paperless web UI or with restore_from_trash. "
)


@dataclass(frozen=True, slots=True)
class Bounds:
    """How much one check reads: pages and their size.

    Attributes:
        documents: Documents with the file that are read; more are only counted.
        trash_entries: Read in one request, because pages of the trash can overlap.
        trash_bytes: The rows of the trash carry the whole text of the documents, so this is what
            bounds the request. A larger trash is not checked, and that is said (decision-52).
        trashed_versions: Deleted versions, each of which costs a request of its own.
        seconds: The whole check. Many clients give a call 60 seconds; a wait of 30 still fits.
    """

    documents: int = 100
    version_pages: int = 10
    version_page: int = 500
    trash_entries: int = 500
    trash_bytes: int = 64 * MIB
    trashed_versions: int = 20
    seconds: float = 25


class CheckMovedError(PaperlessMcpError):
    """What the check read does not add up, so a document may have been missed."""

    def __init__(self, cause: str) -> None:
        """Create the error; ``cause`` is a clause. Nothing was sent."""
        message = (
            f"The duplicate check did not add up while it ran: {cause}; the file was not uploaded."
        )
        super().__init__(message, hint=AGAIN)


# The causes, as CheckMovedError names them.
PAGE_GONE: Final = "a page of the listing was gone"
VERSIONS_CHANGED: Final = "documents were deleted, restored or shared during the listing"
TRASH_SHORT: Final = "Paperless listed fewer entries of the trash than it counts"
TRASH_CHANGED: Final = "the number of entries in the trash changed"
VERSION_GONE: Final = "a deleted version was restored or removed"


class ChecksumMissingError(PaperlessMcpError):
    """Paperless does not tell the checksum of a file, so it could be the same one."""

    def __init__(self, document_id: int) -> None:
        """Create the error; nothing was sent."""
        message = (
            "The duplicate check failed, because Paperless gave no checksum for document "
            f"{document_id} or one of its versions; the file was not uploaded."
        )
        hint = (
            f"An administrator can check the document with the Paperless sanity checker. {NO_SKIP}"
        )
        super().__init__(message, hint=hint)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The versions and the trash by checksum, as they were read once.

    Attributes:
        trash_count: How many entries the trash had; None if the user may not list it, or if even
            one entry is too large to read.
    """

    matches: Mapping[str, tuple[DuplicateMatch, ...]]
    not_checked: tuple[str, ...]
    trash_count: int | None


@dataclass(slots=True)
class Reading:
    """What was read for the files that are to be checked, see ``DuplicateFinder.read_for``.

    Attributes:
        early: The originals of every file, looked up before the snapshot was taken.
    """

    early: Mapping[str, list[DuplicateMatch]]
    snapshot: Snapshot


@dataclass(frozen=True, slots=True)
class Before:
    """What was found before sending; ``count`` may be more than ``matches`` holds."""

    matches: tuple[DuplicateMatch, ...]
    count: int
    not_checked: tuple[str, ...]

    def where(self) -> str:
        """The first matches in words, and how many more there are."""
        named = ", ".join(describe(m) for m in self.matches[:SHOWN])
        more = self.count - len(self.matches[:SHOWN])
        rest = f"{more} {'more' if named else 'document(s) that could not be named'}"
        return named + (f"{' and ' if named else ''}{rest}" if more else "")

    def checked(self, report: DuplicateReport | None) -> DuplicateCheck:
        """The duplicates of an upload: these, and what its task says once it ended."""
        later = report or DuplicateReport()
        return DuplicateCheck(
            before=self.matches[:SHOWN],
            before_count=self.count,
            after=later.after,
            rejected_for=later.rejected_for,
            not_checked=tuple(dict.fromkeys((*self.not_checked, *later.not_checked))),
        )


@dataclass(slots=True)
class _Scan:
    found: list[tuple[str, DuplicateMatch]] = field(
        default_factory=list[tuple[str, DuplicateMatch]]
    )
    gaps: list[str] = field(default_factory=list[str])
    trash_count: int | None = None


def key_of(match: DuplicateMatch) -> tuple[int, int, bool]:
    """What tells matches apart (a document's own file has its id)."""
    return (match.document_id, match.version_id or match.document_id, match.in_trash)


def describe(match: DuplicateMatch) -> str:
    """A match in words, its title quoted."""
    version = f"version {match.version_id} of " if match.version_id is not None else ""
    title = "" if match.title is None else f" {quoted(match.title)}"
    return f"{version}document {match.document_id}{title}" + (
        " in the trash" if match.in_trash else ""
    )


class DuplicateFinder:
    """Finds where the current user can see a file that Paperless already holds."""

    def __init__(
        self,
        documents: DocumentReader,
        trash: TrashReader,
        *,
        web_url: str,
        bounds: Bounds | None = None,
    ) -> None:
        """Read through the ports; ``web_url`` is the Paperless web UI, for links."""
        self._documents: DocumentReader = documents
        self._trash: TrashReader = trash
        self._web_url: str = web_url
        self._bounds: Bounds = bounds or Bounds()

    async def snapshot(self) -> Snapshot:
        """The versions and the trash, read once within the bounds.

        The trash comes first and ``before`` counts it again, so a document or version that is
        deleted or restored in between is noticed.

        Raises:
            CheckMovedError: What was read does not add up.
            PaperlessMcpError: Paperless could not be read. What the user may not read is a gap.
        """
        scan = _Scan()
        await self._read_trash(scan)
        await self._versions(scan)
        matches: dict[str, tuple[DuplicateMatch, ...]] = {}
        for checksum, match in scan.found:
            matches[checksum] = (*matches.get(checksum, ()), match)
        return Snapshot(matches=matches, not_checked=tuple(scan.gaps), trash_count=scan.trash_count)

    async def before(
        self, sha256: str, snapshot: Snapshot, *, own: DocumentRecord | None = None
    ) -> Before:
        """Where the file ``sha256`` is already; ``own`` is the document that gets a version.

        Raises:
            CheckMovedError: The trash changed since the snapshot.
            PaperlessMcpError: Paperless could not be read. What the user may not read is a gap.
        """
        gaps: list[str] = []
        matches: list[DuplicateMatch] = []
        more = 0
        try:
            matches, more = await self._originals(sha256)
        except PermissionDenied:
            gaps.append("Documents: this user may not list them.")
        matches += [] if own is None else self._of(own, sha256)
        counted = snapshot.trash_count
        if counted is not None and await self._count_trash() != counted:
            raise CheckMovedError(TRASH_CHANGED)
        unique = _unique([*matches, *snapshot.matches.get(sha256, ())])
        return Before(
            matches=tuple(sorted(unique, key=lambda m: m.in_trash)),
            count=len(unique) + more,
            not_checked=(UNCOMPARED, ALTERED, *gaps, *snapshot.not_checked, OWN_TRASH, HIDDEN),
        )

    async def cleared(
        self, content: bytes, action: str, *, confirm: bool, own: DocumentRecord | None = None
    ) -> Before:
        """Look for ``content`` before ``action`` (a verb phrase) sends it.

        Raises:
            ConfirmationRequired: Paperless already holds the file and ``confirm`` is not set.
            CheckMovedError: Documents moved while they were read, twice.
            UpstreamTimeout: The check took too long.
            PaperlessMcpError: The check could not run, so nothing may be sent.
        """
        sha256 = hashlib.sha256(content).hexdigest()
        async with self._in_time():
            found = await self._checked(sha256, own)
        if found.count and not confirm:
            trash = RESTORABLE if any(m.in_trash for m in found.matches) else ""
            if (unread := self._trash_unread()) in found.not_checked:
                trash += f"{unread} "
            ConfirmationRequired.require(
                (
                    f"{action} although the same file is already in Paperless: {found.where()}. "
                    f"{trash}Paperless may reject it, if it is configured to"
                ),
                scope=(sha256, found.count, *sorted(key_of(m) for m in found.matches)),
            )
        return found

    async def read_for(self, checksums: Iterable[str]) -> Reading:
        """The originals of several files, then the versions and the trash once for all of them.

        Raises:
            CheckMovedError: Documents moved while they were read, twice.
            UpstreamTimeout: It took too long.
            PaperlessMcpError: The check could not run, so nothing may be sent.
        """
        shas = tuple(checksums)
        async with self._in_time():
            try:
                return await self._read(shas)
            except CheckMovedError:
                return await self._read(shas)

    async def find(self, sha256: str, reading: Reading) -> Before:
        """Where one of the files of ``reading`` is already.

        A trash that changed since is a reason to read everything again, once, for the files
        that follow, too. A version that others add after the reading is missed, like an upload
        at the same time.

        Raises:
            CheckMovedError: Documents moved while they were read again.
            UpstreamTimeout: The check took too long.
            PaperlessMcpError: The check could not run, so the file may not be sent.
        """
        async with self._in_time():
            try:
                return await self._found(sha256, reading)
            except CheckMovedError:
                fresh = await self._read(reading.early)
                reading.early, reading.snapshot = fresh.early, fresh.snapshot
                return await self._found(sha256, reading)

    @asynccontextmanager
    async def _in_time(self) -> AsyncGenerator[None]:
        try:
            async with asyncio.timeout(self._bounds.seconds):
                yield
        except TimeoutError:
            message = (
                f"The duplicate check took longer than {self._bounds.seconds:g} seconds; the file "
                "was not uploaded."
            )
            raise UpstreamTimeout(message, hint=AGAIN) from None

    async def _checked(self, sha256: str, own: DocumentRecord | None) -> Before:
        """One check, and a second one if documents moved while the first one read them."""
        try:
            return await self._found(sha256, await self._read((sha256,)), own)
        except CheckMovedError:
            return await self._found(sha256, await self._read((sha256,)), own)

    async def _read(self, checksums: Iterable[str]) -> Reading:
        early: dict[str, list[DuplicateMatch]] = {}
        for sha256 in checksums:
            try:
                early[sha256], _ = await self._originals(sha256)
            except PermissionDenied:
                early[sha256] = []  # ``before`` names the gap
        return Reading(early, await self.snapshot())

    async def _found(
        self, sha256: str, reading: Reading, own: DocumentRecord | None = None
    ) -> Before:
        """The originals were read first and are read last.

        One that is merged into another document in between is a version only after the versions
        were read, and no original any more at the end. A version keeps the id it had.
        """
        found = await self.before(sha256, reading.snapshot, own=own)
        seen = {n for m in found.matches for n in (m.document_id, m.version_id)}
        missed = [m for m in reading.early[sha256] if m.document_id not in seen]
        return replace(found, matches=(*missed, *found.matches), count=found.count + len(missed))

    async def after(self, consumed: int) -> DuplicateReport:
        """The duplicates of a document or version that was just consumed; never raises."""
        try:
            async with asyncio.timeout(self._bounds.seconds):
                root = await self._documents.get_root(consumed)
                if root == consumed:
                    found = await self._reported(root)
                    return DuplicateReport(after=found, not_checked=(VERSIONS_AFTER, HIDDEN))
                found, more = await self._compared(root, consumed)
        except Exception as error:  # noqa: BLE001 -- the file is in Paperless: the task must get out
            logger.info("duplicates could not be read: %s", type(error).__name__)
            found, more = None, 0
        if found is None:
            return DuplicateReport(not_checked=(UNREAD_AFTER,))
        unnamed = (UNNAMED.format(more=more),) if more else ()
        return DuplicateReport(after=found, not_checked=(VERSION_AFTER, *unnamed, HIDDEN))

    async def rejected(self, duplicate_of: int) -> tuple[DuplicateReport, str]:
        """The document Paperless rejected a file for, if the user may see it; never raises.

        Also the message that says so. ``duplicate_of`` is what the task names: a document or
        version, maybe in the trash or hidden from the user.
        """
        try:
            async with asyncio.timeout(self._bounds.seconds):
                match = await self._existing(duplicate_of)
        except NotFound:
            return DuplicateReport(), HIDDEN_REJECTED
        except Exception as error:  # noqa: BLE001 -- the task's result must get out
            logger.info("the duplicate could not be read: %s", type(error).__name__)
            return DuplicateReport(), UNREAD_REJECTED
        return DuplicateReport(rejected_for=match), REJECTED_FOR.format(where=describe(match))

    # --- reading ---------------------------------------------------------------------------------

    async def _originals(self, sha256: str) -> tuple[list[DuplicateMatch], int]:
        """The documents whose original is the file, and how many more there are."""
        page = await self._documents.search_documents(
            DocumentQuery(checksum=sha256, ordering="id"), page=1, page_size=self._bounds.documents
        )
        return [self._live(hit.id, hit.title) for hit in page.hits], page.count - len(page.hits)

    async def _versions(self, scan: _Scan) -> None:
        """The versions of every document, or of the last changed ones if there are too many."""
        try:
            await self._read_versions(scan)
        except PermissionDenied:
            scan.gaps.append("Versions: this user may not list them.")

    async def _read_versions(self, scan: _Scan) -> None:
        bounds = self._bounds
        listed = await self._documents.list_versions(page=1, page_size=bounds.version_page)
        capped = listed.count > bounds.version_pages * bounds.version_page
        if capped:
            listed = await self._documents.list_versions(
                page=1, page_size=bounds.version_page, recent=True
            )
        first, top = listed.count, max((row.id for row in listed.rows), default=0)
        read = set[int]()
        page = 1
        while True:
            for row in listed.rows:
                read.add(row.id)
                scan.found += self._of_row(row)
            if listed.next_page is None or page == bounds.version_pages:
                break
            page += 1
            try:
                listed = await self._documents.list_versions(
                    page=page, page_size=bounds.version_page, recent=capped
                )
            except NotFound:
                raise CheckMovedError(PAGE_GONE) from None
        if capped:
            scan.gaps.append(CAPPED.format(read=len(read), total=first))
            return
        if listed.count > first:  # what was added since has higher ids; anything else did not
            listed = await self._documents.list_versions(page=1, page_size=bounds.version_page)
            for row in listed.rows:
                scan.found += self._of_row(row)
        added = sum(row.id > top for row in listed.rows) if listed.count > first else 0
        if sum(number <= top for number in read) != first or listed.count - added != first:
            raise CheckMovedError(VERSIONS_CHANGED)

    def _of_row(self, row: VersionRow) -> list[tuple[str, DuplicateMatch]]:
        found: list[tuple[str, DuplicateMatch]] = []
        for version in row.versions:
            if not version.checksum:
                raise ChecksumMissingError(row.id)
            if not version.is_root:  # the originals are found by the filter
                found.append((version.checksum.lower(), self._live(row.id, row.title, version.id)))
        return found

    async def _read_trash(self, scan: _Scan) -> None:
        versions: list[TrashedDocument] = []
        try:
            entries, total = await self._trash_entries()
        except PermissionDenied:
            scan.gaps.append("Trash: this user may not list it.")
            return
        except AnswerTooLarge:
            scan.gaps.append(self._trash_unread())
            scan.trash_count = await self._count_trash()
            return
        scan.trash_count = total
        for entry in entries:
            if entry.version_of is not None:
                versions.append(entry)
            elif entry.checksum:
                scan.found.append((entry.checksum.lower(), _trashed(entry)))
            else:
                raise ChecksumMissingError(entry.id)
        if len(entries) < total:
            scan.gaps.append(f"Trash: only {len(entries)} of {total} entries were read.")
        await self._trashed_versions(versions, scan)

    async def _trash_entries(self) -> tuple[tuple[TrashedDocument, ...], int]:
        """The entries within the bounds, and how many there are.

        Raises:
            CheckMovedError: Paperless lists fewer entries than it counts.
            AnswerTooLarge: The texts of the documents are too large.
        """
        listed = await self._read_entries(self._bounds.trash_entries)
        if len({e.id for e in listed.entries}) < min(listed.count, self._bounds.trash_entries):
            raise CheckMovedError(TRASH_SHORT)
        return listed.entries, listed.count

    def _trash_unread(self) -> str:
        bounds = self._bounds
        return TRASH_UNREAD.format(entries=bounds.trash_entries, mib=bounds.trash_bytes // MIB)

    async def _count_trash(self) -> int | None:
        """How many entries the trash has; None if even one is too large to read."""
        try:
            return (await self._read_entries(1)).count
        except AnswerTooLarge:
            return None

    async def _read_entries(self, entries: int) -> TrashPage:
        most = self._bounds.trash_bytes
        try:
            return await self._trash.read_trash(entries=entries, max_bytes=most)
        except AnswerCompressed as error:
            message = f"{error.message} The file was not uploaded."
            raise AnswerCompressed(message, hint=f"{error.hint} {NO_SKIP}") from None

    async def _trashed_versions(self, entries: list[TrashedDocument], scan: _Scan) -> None:
        """Deleted versions: their checksum is in the metadata of the document, one by one."""
        unread = 0
        for entry in entries[: self._bounds.trashed_versions]:
            root = entry.version_of or entry.id
            try:
                checksum = await self._documents.get_version_checksum(root, entry.id)
            except NotFound:
                raise CheckMovedError(VERSION_GONE) from None
            except PermissionDenied:
                unread += 1
                continue
            if not checksum:
                raise ChecksumMissingError(root)
            scan.found.append((checksum.lower(), _trashed(entry)))
        if unread:
            scan.gaps.append(f"Trash: deleted versions this user could not read: {unread}.")
        if len(entries) > (most := self._bounds.trashed_versions):
            scan.gaps.append(f"Trash: only {most} of {len(entries)} deleted versions were read.")

    async def _reported(self, document: int) -> tuple[DuplicateMatch, ...]:
        """What Paperless takes for duplicates of a document."""
        return tuple(
            self._live(d.id, d.title)
            if d.deleted_at is None
            else DuplicateMatch(
                document_id=d.id, title=d.title, in_trash=True, deleted_at=d.deleted_at
            )
            for d in await self._documents.get_duplicates(document)
        )

    async def _compared(
        self, root: int, version: int
    ) -> tuple[tuple[DuplicateMatch, ...] | None, int]:
        """Where else the file of a version is, and how many more; None: its checksum is unknown."""
        record = await self._documents.get_document(root)
        checksum = next((v.checksum for v in record.versions if v.id == version), None)
        if not checksum:
            return None, 0
        originals, more = await self._originals(checksum)
        found = _unique([*originals, *self._of(record, checksum.lower())])
        return tuple(m for m in found if m.version_id != version), more

    async def _existing(self, number: int) -> DuplicateMatch:
        """The document or version ``number``, in the trash or not.

        Raises:
            NotFound: It does not exist, or the user may not see it.
        """
        root = await self._documents.get_root(number)
        version = None if number == root else number
        try:
            record = await self._documents.get_document(root)
        except NotFound:
            return await self._in_trash(root, version)
        live = version is None or version in {v.id for v in record.versions}
        if live:
            return self._live(root, record.title, version)
        return DuplicateMatch(
            document_id=root, title=record.title, version_id=version, in_trash=True
        )

    async def _in_trash(self, root: int, version: int | None) -> DuplicateMatch:
        """A document in the trash; its title only if the user's trash lists it."""
        try:
            entries, _ = await self._trash_entries()
        except CheckMovedError, AnswerTooLarge, PermissionDenied:
            entries = ()
        entry = next((e for e in entries if e.id == root), None)
        return DuplicateMatch(
            document_id=root,
            title=entry.title if entry else None,
            version_id=version,
            in_trash=True,
            deleted_at=entry.deleted_at if entry else None,
        )

    # --- matches ---------------------------------------------------------------------------------

    def _live(self, document: int, title: str, version: int | None = None) -> DuplicateMatch:
        return DuplicateMatch(
            document_id=document,
            title=title,
            version_id=version,
            web_url=document_link(self._web_url, document),
        )

    def _of(self, record: DocumentRecord, sha256: str) -> list[DuplicateMatch]:
        """The versions of one document with the file; its root is the document itself."""
        if not all(v.checksum for v in record.versions):
            raise ChecksumMissingError(record.id)
        return [
            self._live(record.id, record.title, None if v.is_root else v.id)
            for v in record.versions
            if (v.checksum or "").lower() == sha256
        ]


def _trashed(entry: TrashedDocument) -> DuplicateMatch:
    return DuplicateMatch(
        document_id=entry.version_of or entry.id,
        title=entry.title,
        version_id=None if entry.version_of is None else entry.id,
        in_trash=True,
        deleted_at=entry.deleted_at,
    )


def _unique(matches: Iterable[DuplicateMatch]) -> tuple[DuplicateMatch, ...]:
    return tuple({key_of(match): match for match in matches}.values())
