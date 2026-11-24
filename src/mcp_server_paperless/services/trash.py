"""The Paperless trash: listing it, restoring from it and emptying it behind confirm.

Restoring is the safe direction and takes no confirmation. Emptying removes documents and
deleted versions for good (decision-6, decision-15), so it never defaults to anything: it needs
ids, a filter or ``all=true``, and a confirmation in every case. The refusal names what would be
destroyed. A filter never acts by itself: it shows its matches, and only ids that were shown are
acted on (decision-55). Both actions look the ids up in the trash first, so a caller is never asked
to confirm what cannot happen, and Paperless is only ever sent explicit ids: a request without ids
means "everything" to it. What became of each entry is read afterwards: Paperless answers OK also
when emptying failed.
"""

from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.trash import (
    MAX_IDS,
    EntryOutcome,
    RestoredEntry,
    TrashedDocument,
    TrashEmptied,
    TrashEntry,
    TrashList,
    TrashRestored,
)
from mcp_server_paperless.services.document_edits import COUNTED
from mcp_server_paperless.services.documents import document_link, known_items, maybe_ref

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence

    from mcp_server_paperless.domain.ports import DocumentReader, TrashReader, TrashWriter
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.domain.trash import Outcome, TrashFilter, TrashItem
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver
    from mcp_server_paperless.services.trash_scan import Selection, TrashScan

# Entries a confirmation names one by one; the rest are counted.
SHOWN: Final = 10
# Most entries ``all=true`` handles at once (it names every id to Paperless).
MAX_ALL: Final = 1000

LOST = (
    "They cannot be restored afterwards: the files, text, notes and metadata of documents, and "
    "the deleted versions, are lost for good"
)


# What an ``all=true`` confirmation adds: it empties what is there when it is confirmed.
WHEN_CONFIRMED = (
    "Confirming empties whatever the trash holds at that moment; pass document_ids instead to "
    "delete exactly these entries"
)
CHECKED: Final = (
    "Paperless checks all ids before it changes any, but a failure half-way can leave a part."
)
NO_ID: Final = "Paperless does not say which id it refuses, and every one is still listed."
ONE_OF: Final = "Pass exactly one of document_ids and filter."
ONE_OF_THREE: Final = "Pass exactly one of document_ids, filter and all=true."
NO_FILTER: Final = "An empty filter would match every entry: nothing was changed."
NO_FILTER_HINT: Final = "Give at least one field of the filter."


def trash_link(web_url: str) -> str:
    """The trash page of the web UI; entries have none of their own."""
    return f"{web_url.rstrip('/')}/trash"


def _counted(count: int, noun: str) -> str:
    """``1 document``, ``2 documents``, ``1 entry``, ``2 entries``."""
    if count == 1:
        return f"1 {noun}"
    return f"{count} {noun.removesuffix('y')}ies" if noun.endswith("y") else f"{count} {noun}s"


def _described(entry: TrashItem, *, deleted: bool = True) -> str:
    """One entry in words: its id and title, what it is, and optionally when it was deleted."""
    when = (
        f" (deleted {entry.deleted_at.date().isoformat()})"
        if deleted and isinstance(entry, TrashedDocument)
        else ""
    )
    if entry.version_of is None:
        return f"document {entry.id} {quoted(entry.title)}{when}"
    return f"version {entry.id} of document {entry.version_of} {quoted(entry.title)}{when}"


def _joined(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _rest(entries: Sequence[EntryOutcome]) -> str:
    """What the trash showed of entries that are not done; empty if all are."""
    parts: list[str] = []
    for outcome, label in (
        ("still_in_trash", "Still in the trash"),
        ("unverified", "Could not be verified"),
    ):
        found = [_described(e) for e in entries if e.outcome == outcome]
        if found:
            parts.append(f" {label}: {_joined(found)}.")
    return f"{''.join(parts)} {CHECKED}" if parts else ""


def _selected_effect(selection: Selection, shown: Sequence[TrashedDocument]) -> str:
    """The entries a filter found, every one that can be acted on named."""
    more = len(selection.entries) - len(shown)
    rest = f" ({more} more match; repeat for them afterwards)" if more else ""
    gap = f". {selection.notice}".rstrip(".") if selection.notice else ""
    return f"{_joined([_described(e) for e in shown])}{rest}{gap}"


class TrashService:
    """Lists, restores and empties the Paperless trash for the current user."""

    def __init__(  # noqa: PLR0913 -- the ports and what they need
        self,
        reader: TrashReader,
        writer: TrashWriter,
        documents: DocumentReader,
        scan: TrashScan,
        taxonomy: TaxonomyResolver,
        *,
        web_url: str,
        max_all: int = MAX_ALL,
    ) -> None:
        """Read through ``reader`` and ``scan``, change through ``writer``.

        ``documents`` tells what became of an entry. ``taxonomy`` forgets the document counts
        after a restore; ``web_url`` is the Paperless web UI, for links. ``max_all`` is the most
        entries ``all=true`` empties at once.
        """
        self._reader: TrashReader = reader
        self._writer: TrashWriter = writer
        self._documents: DocumentReader = documents
        self._scan: TrashScan = scan
        self._taxonomy: TaxonomyResolver = taxonomy
        self._web_url: str = web_url
        self._max_all: int = max_all

    async def list(self, wanted: TrashFilter, *, page: int, page_size: int) -> TrashList:
        """One page of the trash, or of the entries that match ``wanted``.

        Raises:
            NotFound: ``page`` is beyond the last page, or no such document type.
            Validation: The days contradict each other.
            PermissionDenied: The user may not view documents.
        """
        if not wanted:
            found = await self._reader.list_trash(page=page, page_size=page_size)
            return TrashList(
                web_url=trash_link(self._web_url),
                count=found.count,
                page=page,
                next_page=found.next_page,
                entries=await self._rows(found.entries),
            )
        selection = await self._scan.select(wanted)
        start, count = (page - 1) * page_size, len(selection.entries)
        if page > 1 and start >= count:
            message = f"Page {page} of the matching entries does not exist."
            raise NotFound(message, hint="List with page=1; count and next_page tell how far.")
        return TrashList(
            web_url=trash_link(self._web_url),
            count=count,
            page=page,
            next_page=page + 1 if start + page_size < count else None,
            entries=await self._rows(selection.entries[start : start + page_size]),
            scanned=None if selection.by_id else len(selection.scan.entries),
            truncated=selection.truncated,
            notice=selection.notice,
        )

    async def _rows(self, entries: Sequence[TrashedDocument]) -> tuple[TrashEntry, ...]:
        kinds: tuple[TaxonomyKind, ...] = ("document_type", "user") if entries else ()
        known: dict[TaxonomyKind, dict[int, TaxonomyItem]] = {
            kind: await known_items(self._taxonomy, kind) for kind in kinds
        }
        return tuple(
            TrashEntry(
                id=e.id,
                title=e.title,
                version_of=e.version_of,
                deleted_at=e.deleted_at,
                created=e.created,
                mime_type=e.mime_type,
                document_type=maybe_ref(known, "document_type", e.document_type),
                owner=maybe_ref(known, "user", e.owner),
                checksum=e.checksum,
            )
            for e in entries
        )

    async def restore(
        self, document_ids: Sequence[int] | None, wanted: TrashFilter | None = None
    ) -> TrashRestored:
        """Take the entries out of the trash: documents come back, versions are re-attached.

        With ``wanted`` nothing is restored: the refusal names the ids to pass.

        Raises:
            Validation: No id, more than ``MAX_IDS``, or ``wanted``, which only shows.
            NotFound: An id is not in the trash (or not one the user may see). Nothing is
                restored.
            PermissionDenied: The user may not delete documents, or one of these.
        """
        if (document_ids is None) == (wanted is None):
            raise Validation(ONE_OF, hint="Pass the ids from list_trash, or a filter to see them.")
        if wanted is not None:
            selection, shown = await self._matches(wanted, "restored")
            count = _counted(len(selection.entries), "entry")
            message = (
                f"Nothing was restored. The filter matches {count}: "
                f"{_selected_effect(selection, shown)}."
            )
            hint = (
                "Show them to the user, then call again without filter and with "
                f"document_ids={[e.id for e in shown]}."
            )
            raise Validation(message, hint=hint)
        ids = self._distinct(document_ids or ())
        entries = await self._scan.require(ids)
        outcomes = await self._act(self._writer.restore_from_trash, entries, "restored")
        for kind in COUNTED:
            self._taxonomy.invalidate(kind)
        done = [e for e in entries if outcomes[e.id] == "restored"]
        documents = [e for e in done if e.version_of is None]
        versions = [e for e in done if e.version_of is not None]
        kinds = ((documents, "document"), (versions, "version"))
        counts = [_counted(len(found), noun) for found, noun in kinds if found]
        parts = [
            _described(e, deleted=False)
            + (" (attached to the document again)" if e.version_of else "")
            for e in done
        ]
        said = (
            f"Restored {' and '.join(counts)} from the trash: {_joined(parts)}."
            if done
            else "No entry was seen to be restored."
        )
        restored = tuple(
            RestoredEntry(
                id=e.id,
                title=e.title,
                version_of=e.version_of,
                outcome=outcomes[e.id],
                web_url=document_link(self._web_url, e.version_of or e.id),
            )
            for e in entries
        )
        return TrashRestored(entries=restored, message=said + _rest(restored))

    async def empty(
        self,
        document_ids: Sequence[int] | None,
        wanted: TrashFilter | None = None,
        *,
        everything: bool,
        confirm: bool = False,
    ) -> TrashEmptied:
        """Remove entries for good: the named ones, those ``wanted`` matches, or every one.

        Without ``confirm`` nothing changes and the refusal names what would be destroyed.
        ``confirm`` does not count for ``wanted``: what it matches has to be shown first, so it
        takes the user's answer in a dialog, or a second call with the ids.

        Raises:
            ConfirmationRequired: ``confirm`` is false, or ``wanted`` was not accepted in a
                dialog. Nothing was changed.
            Validation: Not exactly one of the three; too many ids; ``everything`` on a trash
                larger than the limit; ``wanted`` matches nothing.
            NotFound: An id is not in the trash (or not one the user may see).
            PermissionDenied: The user may not delete documents, or one of these.
        """
        if not document_ids and wanted is None and not everything:
            message = "Name what to empty: nothing was emptied."
            hint = "Pass document_ids from list_trash, or all=true to empty the whole trash."
            raise Validation(message, hint=hint)
        if [bool(document_ids), wanted is not None, everything].count(True) != 1:
            raise Validation(
                ONE_OF_THREE, hint="Use document_ids for some entries, all=true for every one."
            )
        if wanted is not None:
            entries = await self._accepted(wanted)
        else:
            entries = await (
                self._scan.require(self._distinct(document_ids))
                if document_ids
                else self._everything()
            )
            if not entries:
                return TrashEmptied(
                    document_ids=(),
                    count=0,
                    entries=(),
                    message="The trash is empty: nothing was deleted.",
                )
            if not confirm:
                ConfirmationRequired.require(
                    self._effect(entries, everything=everything),
                    scope=sorted(e.id for e in entries),
                )
        outcomes = await self._act(self._writer.empty_trash, entries, "deleted")
        done = [e for e in entries if outcomes[e.id] == "deleted"]
        if not done:
            message = "No entry was seen to be deleted."
        elif everything and len(done) == len(entries):
            message = (
                f"Permanently deleted ALL {_counted(len(entries), 'entry')} of the trash (every "
                "entry the user can see) as it was when you confirmed."
            )
        else:
            message = (
                f"Permanently deleted {_counted(len(done), 'entry')} from the trash: "
                f"{_joined([_described(e, deleted=False) for e in done])}."
            )
        results = tuple(
            EntryOutcome(id=e.id, title=e.title, version_of=e.version_of, outcome=outcomes[e.id])
            for e in entries
        )
        return TrashEmptied(
            document_ids=tuple(e.id for e in done),
            count=len(done),
            entries=results,
            message=message + _rest(results),
        )

    async def _matches(
        self, wanted: TrashFilter, done: str
    ) -> tuple[Selection, tuple[TrashedDocument, ...]]:
        """What ``wanted`` matches and the part one call can act on, newest deletion first.

        Raises:
            Validation: The filter is empty or matches nothing.
        """
        if not wanted:
            raise Validation(NO_FILTER, hint=NO_FILTER_HINT)
        selection = await self._scan.select(wanted)
        if not selection.entries:
            message = " ".join(
                [f"The filter matches no entry: nothing was {done}.", selection.notice or ""]
            ).strip()
            raise Validation(message, hint="Look with list_trash.")
        newest = sorted(selection.entries, key=lambda e: (e.deleted_at, e.id), reverse=True)
        return selection, tuple(newest[:MAX_IDS])

    async def _accepted(self, wanted: TrashFilter) -> tuple[TrashedDocument, ...]:
        """The entries ``wanted`` matches, if the user accepted exactly them in a dialog."""
        selection, shown = await self._matches(wanted, "deleted")
        ids = [e.id for e in shown]
        effect = (
            f"permanently delete {_counted(len(shown), 'entry')} from the Paperless trash: "
            f"{_selected_effect(selection, shown)}. {LOST}"
        )
        hint = (
            "Show them to the user; once they agree, call again without filter and with "
            f"document_ids={ids} and confirm=true. confirm=true does not count with a filter."
        )
        ConfirmationRequired.require(effect, scope=sorted(ids), hint=hint)
        return shown

    async def _act(
        self,
        write: Callable[[Sequence[int]], Coroutine[object, object, None]],
        entries: Sequence[TrashedDocument],
        done: Outcome,
    ) -> dict[int, Outcome]:
        """Send the ids, then read what became of each entry, also after a failure.

        Raises:
            Validation: Paperless refused; the ids that are no longer listed are named.
            PermissionDenied: Likewise.
            PaperlessMcpError: The request failed and no entry was seen to be done.
        """
        failure: PaperlessMcpError | None = None
        try:
            await write([e.id for e in entries])
        except (Validation, PermissionDenied) as error:
            message = f"{error.message} {await self._blocking(entries)}"
            raise type(error)(message, hint=error.hint) from None
        except PaperlessMcpError as error:
            failure = error
        outcomes = await self._outcomes(entries, done)
        if failure is not None and done not in outcomes.values():
            raise failure
        return outcomes

    async def _blocking(self, entries: Sequence[TrashedDocument]) -> str:
        try:
            found, _ = await self._scan.locate([e.id for e in entries])
        except PaperlessMcpError:
            return "Which id it refuses is unknown."
        gone = [str(e.id) for e in entries if e.id not in found]
        return f"No longer in the trash: {', '.join(gone)}." if gone else NO_ID

    async def _outcomes(
        self, entries: Sequence[TrashedDocument], done: Outcome
    ) -> dict[int, Outcome]:
        """What the trash shows of each entry now.

        Paperless also reports success when emptying failed, so its answer proves nothing.
        """
        unread: dict[int, Outcome] = {e.id: "unverified" for e in entries}
        try:
            scan = await self._scan.first()
        except AnswerTooLarge, AnswerCompressed:
            scan = None
        except PaperlessMcpError:
            return unread
        listed = {e.id for e in scan.entries} if scan else set[int]()
        rest: Outcome = done if scan and scan.complete else "unverified"
        outcomes: dict[int, Outcome] = dict.fromkeys(unread, rest)
        outcomes.update(dict.fromkeys(listed & unread.keys(), "still_in_trash"))
        if rest == done:
            return outcomes
        try:
            for entry in [e for e in entries if e.id not in listed][:MAX_IDS]:
                outcomes[entry.id] = await self._outcome(entry, done)
        except PaperlessMcpError:
            pass  # the rest stays unverified
        return outcomes

    async def _outcome(self, entry: TrashedDocument, done: Outcome) -> Outcome:
        """What became of one entry that is not among those that could be read at once."""
        try:
            if done == "restored":
                document = await self._documents.get_document(entry.version_of or entry.id)
                shown = (
                    {entry.id} if entry.version_of is None else {v.id for v in document.versions}
                )
                return "restored" if entry.id in shown else "unverified"
            _ = await self._documents.get_root(entry.id)
        except NotFound:
            return "deleted" if done == "deleted" else "unverified"
        return "still_in_trash"

    @staticmethod
    def _distinct(document_ids: Sequence[int]) -> list[int]:
        """The ids once each, in the order given.

        Raises:
            Validation: No id, or more than ``MAX_IDS``.
        """
        ids = list(dict.fromkeys(document_ids))
        if not ids:
            message = "Name at least one trash entry."
            raise Validation(message, hint="Pass the ids from list_trash.")
        if len(ids) > MAX_IDS:
            message = f"Name at most {MAX_IDS} trash entries at once; got {len(ids)}."
            raise Validation(message, hint="Work in several steps.")
        return ids

    async def _everything(self) -> list[TrashedDocument]:
        """Every entry the user sees, in one request, so that the count and the ids agree.

        Raises:
            Validation: The trash holds more than ``max_all`` entries.
        """
        listed = await self._reader.list_trash(page=1, page_size=self._max_all + 1)
        if listed.count > self._max_all:
            message = (
                f"The trash holds {listed.count} entries; all=true handles at most "
                f"{self._max_all} at once."
            )
            raise Validation(message, hint="Empty in steps with document_ids from list_trash.")
        # Offset paging has no tie-breaker in Paperless, so a read may repeat an entry.
        return list({e.id: e for e in listed.entries}.values())

    @staticmethod
    def _effect(entries: Sequence[TrashedDocument], *, everything: bool) -> str:
        """What confirming would do, naming the first ``SHOWN`` entries and counting the rest."""
        shown = [_described(e) for e in entries[:SHOWN]]
        if len(entries) > SHOWN:
            shown.append(f"{len(entries) - SHOWN} more")
        if everything:
            head = (
                f"permanently delete ALL {_counted(len(entries), 'entry')} of the Paperless "
                "trash (every entry the user can see)"
            )
        else:
            head = f"permanently delete {_counted(len(entries), 'entry')} from the Paperless trash"
        tail = f". {WHEN_CONFIRMED}" if everything else ""
        return f"{head}: {_joined(shown)}. {LOST}{tail}"
