"""Bulk edits of documents: a preview first, a confirmation, one request, nothing half done.

Documents are chosen by ids, search filters or a saved view (``DocumentSelector``); at most
``MAX_DOCUMENTS`` of them change per call. The editor reads their current state, resolves names to
ids, works out for every document what the operation changes (documents that already are as asked
are left alone and not sent), and then either previews that (``dry_run``), refuses without a
confirmation (decision-15), or sends one request for the documents that change. Paperless checks
the whole batch before it changes anything, so a refusal changes no document.

A filter or a view never acts by itself (decision-69): an accepted dialog is needed, or a second
call that names the ids besides the same filter. A confirmation names every document and covers the
current values of what changes (decision-71).

Residual race: between the read and the request another client may change a document; the request
then applies to its newer state (Paperless merges tags and fields on the server). The tag
hierarchy, like every name, comes from the resolver's cache (a TTL), so a tag moved elsewhere in
the last minutes can still be seen at its old place in the preview.

Change permission: a search hit says whether the user may change a document (object level; the
global permission is not visible). A batch with a document the user may not change is flagged in
the preview and refused before it is sent: Paperless would refuse it as a whole anyway. Deleting
also needs ownership, which a hit does not show; Paperless's refusal then explains.

Paperless runs the change as a task, which can fail half way (custom fields are written document by
document). After a failed, lost or unconfirmed change the documents are read again, once.
"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.bulk import (
    MAX_DOCUMENTS,
    SAMPLE_SIZE,
    BulkOutcomes,
    DocumentBulkReport,
    DocumentSample,
    ReprocessChange,
    TrashDocuments,
)
from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    AuthenticationFailed,
    ConfirmationRequired,
    Conflict,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    TaskFailed,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.services.bulk_operations import (
    BulkOperations,
    named,
    require_all,
    titled,
)
from mcp_server_paperless.services.documents import document_link
from mcp_server_paperless.services.removal import WAY_BACK
from mcp_server_paperless.services.taxonomy_edits import documents as count_documents

if TYPE_CHECKING:
    from mcp_server_paperless.domain.bulk import BulkOperation
    from mcp_server_paperless.domain.documents import DocumentHit
    from mcp_server_paperless.domain.ports import BulkWriter, DocumentReader
    from mcp_server_paperless.domain.selection import Selection
    from mcp_server_paperless.domain.tasks import Unconfirmed
    from mcp_server_paperless.services.bulk_operations import Operation
    from mcp_server_paperless.services.selection import DocumentSelector, Selected
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

AS_ASKED: Final = "as asked"
DRY_RUN: Final = "Dry run, nothing was changed."
TO_GO_AHEAD: Final = (
    "To go ahead, call again without dry_run; pass confirm=true only if the user already agreed."
)
TO_GO_AHEAD_BY_IDS: Final = (
    "To go ahead, call again without dry_run and with document_ids set to selected_ids besides the "
    "same filters or saved_view; pass confirm=true only if the user already agreed."
)
NEXT_CALL: Final = (
    "Show the user these documents and the effect. Once they agree, call again with the same "
    "filters (or saved_view), exclude_ids and operation, and with document_ids=[{ids}] and "
    "confirm=true: exactly these are changed, and none if one of them no longer matches. With "
    "filters or saved_view alone, confirm=true is not enough."
)
READ_AGAIN: Final = "Read the documents again before repeating the call."
STILL_RUNNING: Final = " What may still run can change them after that."
# What Paperless answers before it starts anything: nothing was changed.
REFUSALS: Final = (AuthenticationFailed, Conflict, NotFound, PermissionDenied, Validation)


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What an operation does to a selection, for the report."""

    resolved: Operation
    chosen: Selected
    # The documents of this call, and those that match besides and need the change, too.
    changing: list[DocumentHit]
    left: list[DocumentHit]
    effects: dict[int, str]
    others: list[tuple[int, str]]
    blocked: list[DocumentHit]
    # Whether a filter or a view stands alone, and whether one stands with ids.
    alone: bool
    beside: bool

    @property
    def unchanged(self) -> int:
        """How many selected documents already are as asked."""
        rest = len(self.left) if not self.beside else 0
        return len(self.chosen.hits) - len(self.changing) - rest

    @property
    def effect(self) -> str:
        """What confirming would do, with every document."""
        resolved = self.resolved
        effect = resolved.phrase(named(titled(self.changing)))
        if resolved.hidden_children:
            noun = "document" if len(self.changing) == 1 else "documents"
            effect += f"; child tags you cannot see (other users') come off the {noun} with them"
        if self.others:
            effect += (
                " and, because document links are mirrored, also changes "
                f"{named(self.others, ' besides')}"
            )
        if self.chosen.excluded_unchecked:
            effect += (
                f"; whether {', '.join(self.chosen.excluded_unchecked)} still exists, which the "
                "selection excludes, could not be checked: if not, more matches than was meant"
            )
        return effect

    @property
    def scope(self) -> object:
        """What the text does not tell apart."""
        # The documents that links are mirrored on follow from both.
        touched = [(hit.id, self.resolved.touched(hit)) for hit in self.changing]
        return touched, self.resolved.change

    def rest(self, *, applied: bool) -> str:
        """What also matches and is not touched, as a sentence."""
        count = len(self.left)
        if not count:
            return ""
        noun = "document matches" if count == 1 else "documents match"
        if self.alone:
            return f" {count} more {noun}: call again with the same filters or saved_view to go on."
        verb = ("was" if count == 1 else "were") if applied else ("is" if count == 1 else "are")
        return f" {count} other {noun} and {verb} not touched (also_matching)."


class BulkDocumentEditor:
    """Previews and applies one operation to many documents."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: BulkWriter,
        selector: DocumentSelector,
        taxonomy: TaxonomyResolver,
        *,
        web_url: str,
    ) -> None:
        """Read what ``selector`` chooses, write through ``writer``; ``web_url`` makes links."""
        self._reader: DocumentReader = reader
        self._writer: BulkWriter = writer
        self._selector: DocumentSelector = selector
        self._taxonomy: TaxonomyResolver = taxonomy
        self._operations: BulkOperations = BulkOperations(reader, taxonomy)
        self._web_url: str = web_url

    async def edit(
        self, selection: Selection, operation: BulkOperation, *, dry_run: bool, confirm: bool
    ) -> DocumentBulkReport:
        """Preview (``dry_run``) or apply ``operation`` to the documents of ``selection``.

        Documents that already are as asked are counted but not sent. Nothing is written for a
        preview, without a confirmation, or when no document changes. ``confirm`` confirms only
        documents that are named by id; a filter or a view alone needs an accepted dialog.

        Raises:
            Validation: The selection is refused (see ``DocumentSelector.select``), holds more than
                ``MAX_DOCUMENTS`` ids, or excludes what the user's list does not hold; the
                operation is refused (see ``BulkOperations.resolve``), or a tag removal would take
                child tags along.
            NotFound: A document, view or name is unknown or hidden from the user.
            Ambiguous: A name matches more than one object.
            ConfirmationRequired: The operation would change documents and is not confirmed.
            PermissionDenied: The user may not change (or delete) one of the documents; Paperless
                then changed none.
            PaperlessMcpError: The change failed or its answer was lost; the message names what was
                seen afterwards.
        """
        outcome = await self._outcome(selection, operation)
        resolved, changing = outcome.resolved, outcome.changing
        if outcome.blocked and not dry_run:
            message = (
                f"You may not change {named(titled(outcome.blocked))}, so Paperless would refuse "
                "the whole batch; nothing was sent."
            )
            hint = "Leave those documents out, or ask their owner for change permission."
            raise PermissionDenied(message, hint=hint)
        if changing and not dry_run and (outcome.alone or not confirm):
            await self._require(outcome)
        applied = bool(changing) and not dry_run
        unconfirmed = await self._send(resolved, changing) if applied else None
        seen = None if unconfirmed is None else await self._seen(resolved, changing)
        return self._report(
            outcome, dry_run=dry_run, applied=applied, unconfirmed=unconfirmed, seen=seen
        )

    async def _outcome(self, selection: Selection, operation: BulkOperation) -> _Outcome:
        """What ``operation`` would do to the documents of ``selection`` now."""
        by_filter = selection.filters is not None or selection.saved_view is not None
        ids = dict.fromkeys(selection.document_ids)
        if len(ids) > MAX_DOCUMENTS or not (ids or by_filter):
            message = f"Select between 1 and {MAX_DOCUMENTS} documents per call."
            hint = (
                "Search with page_size 100 and edit the result page by page, or narrow the search."
            )
            raise Validation(message, hint=hint)
        resolved = await self._operations.resolve(operation)
        chosen = await self._selector.select(selection, change=True)
        require_all(chosen.missing)
        if chosen.excluded_absent:
            message = (
                f"Nothing was changed: the selection excludes {', '.join(chosen.excluded_absent)}, "
                "which you cannot see. If it was deleted, it excludes nothing and more matches "
                "than was meant."
            )
            hint = (
                "Use filters or a view without it, or pass the document_ids that read_documents "
                "shows instead of the filters."
            )
            raise Validation(message, hint=hint)
        effects = resolved.effects(list(chosen.hits))
        changing = [hit for hit in chosen.hits if hit.id in effects]
        if isinstance(resolved.change, ReprocessChange) and len(changing) > MAX_DOCUMENTS:
            message = (
                f"{len(changing)} documents match, and reprocessed ones still match: at most "
                f"{MAX_DOCUMENTS} may be reprocessed by filters or saved_view."
            )
            hint = "Narrow the filters, or add document_ids: read_documents shows them."
            raise Validation(message, hint=hint)
        changing, left = changing[:MAX_DOCUMENTS], changing[MAX_DOCUMENTS:]
        mirrored = resolved.mirrored(changing) - {hit.id for hit in changing}
        return _Outcome(
            resolved=resolved,
            chosen=chosen,
            changing=changing,
            left=left or [hit for hit in chosen.others if _needs(resolved, hit)],
            effects=effects,
            others=await self._others(mirrored),
            blocked=[hit for hit in changing if hit.user_can_change is False],
            alone=by_filter and not ids,
            beside=by_filter and bool(ids),
        )

    async def _require(self, outcome: _Outcome) -> None:
        """Refuse, unless the user accepted exactly this.

        Raises:
            ConfirmationRequired: They did not; the hint names the next call.
        """
        ids = [hit.id for hit in outcome.changing]
        files = await self._reader.get_newest_versions(ids) if outcome.resolved.on_files else {}
        hint = NEXT_CALL.format(ids=", ".join(map(str, ids))) if outcome.alone else CONFIRM_HINT
        ConfirmationRequired.require(
            outcome.effect, scope=(outcome.scope, sorted(files.items())), hint=hint
        )

    async def _send(self, resolved: Operation, changing: list[DocumentHit]) -> Unconfirmed | None:
        """Make the change; a failure that may have changed something says what was seen."""
        try:
            return await self._writer.bulk_edit_documents(
                [hit.id for hit in changing], resolved.change
            )
        except PaperlessMcpError as error:
            failed = isinstance(error, TaskFailed)
            if not failed and (isinstance(error, REFUSALS) or _limited(error)):
                raise
            seen = await self._seen(resolved, changing)
            error.message = f"{error.message} {_told(seen, running=not failed)}"
            error.hint = error.hint if failed else READ_AGAIN
            raise
        finally:
            for kind in resolved.dirty:
                self._taxonomy.invalidate(kind)

    async def _seen(self, resolved: Operation, changing: list[DocumentHit]) -> BulkOutcomes:
        """What became of the documents, read once; never raises, because they may be changed."""
        ids = [hit.id for hit in changing]
        if isinstance(resolved.change, ReprocessChange):
            return BulkOutcomes(unknown=tuple(ids))
        query = DocumentQuery(ids=tuple(ids))
        try:
            page = await self._reader.search_documents(query, page=1, page_size=MAX_DOCUMENTS)
        except PaperlessMcpError:
            return BulkOutcomes(unknown=tuple(ids))
        now = {hit.id: hit for hit in page.hits}
        whole = len(now) == page.count
        found: dict[str, list[int]] = {"changed": [], "not_changed": [], "unknown": []}
        for before in changing:
            after = now.get(before.id)
            if after is None:
                trashed = whole and isinstance(resolved.change, TrashDocuments)
                state = "changed" if trashed else "unknown"
            elif not _needs(resolved, after):
                state = "changed"
            else:
                same = resolved.touched(after) == resolved.touched(before)
                state = "not_changed" if same else "unknown"
            found[state].append(before.id)
        return BulkOutcomes.model_validate(found)

    async def _others(self, ids: set[int]) -> list[tuple[int, str]]:
        """Titles of the other documents a change reaches ('#id' if hidden)."""
        if not ids:
            return []
        titles = await self._reader.get_document_titles(sorted(ids))
        return [(number, titles.get(number, f"#{number}")) for number in sorted(ids)]

    def _samples(self, rows: list[tuple[int, str]], change: str) -> tuple[DocumentSample, ...]:
        return tuple(
            DocumentSample(
                id=number,
                title=title,
                change=change,
                web_url=document_link(self._web_url, number),
            )
            for number, title in rows[:SAMPLE_SIZE]
        )

    def _report(
        self,
        outcome: _Outcome,
        *,
        dry_run: bool,
        applied: bool,
        unconfirmed: Unconfirmed | None,
        seen: BulkOutcomes | None,
    ) -> DocumentBulkReport:
        resolved, changing, others = outcome.resolved, outcome.changing, outcome.others
        total = len(outcome.chosen.hits)
        if outcome.blocked:
            one = len(outcome.blocked) == 1
            message = (
                f"{DRY_RUN} Paperless would refuse this batch, so none would change: you may "
                f"not change {named(titled(outcome.blocked))}. Leave "
                f"{'it' if one else 'them'} out, or ask "
                f"{'its' if one else 'their'} owner for change permission."
            )
        elif not changing:
            message = f"Nothing to change: {_as_asked(total)}."
        elif dry_run:
            rest = ""
            if outcome.unchanged:
                verb = "is" if outcome.unchanged == 1 else "are"
                rest = f"; {count_documents(outcome.unchanged)} already {verb} {AS_ASKED}"
            ahead = TO_GO_AHEAD_BY_IDS if outcome.alone else TO_GO_AHEAD
            message = f"{DRY_RUN} Confirming would {outcome.effect}{rest}. {ahead}"
        elif unconfirmed is not None and seen is not None:
            message = (
                f"Asked Paperless to apply to {count_documents(len(changing))}: "
                f"{resolved.summary}. Not confirmed: Paperless queued task {unconfirmed.task_id}, "
                f"but {unconfirmed.reason}. Read it with get_task and read the documents again "
                f"before repeating the call. {_told(seen, running=True)}"
            )
        else:
            message = f"Applied to {count_documents(len(changing))}: {resolved.summary}."
        if applied and others and unconfirmed is None:
            message += f" Mirrored on {named(others, ' besides')}."
        if applied and isinstance(resolved.change, TrashDocuments):
            message += f" Each {WAY_BACK}"
        if not outcome.blocked:
            message += outcome.rest(applied=applied)
        left = tuple(hit.id for hit in outcome.left)
        return DocumentBulkReport(
            operation=resolved.name,
            summary=resolved.summary,
            dry_run=dry_run,
            applied=applied,
            unconfirmed_task_id=None if unconfirmed is None else unconfirmed.task_id,
            outcomes=seen,
            matched=outcome.chosen.matched,
            requested=total,
            selected_ids=tuple(hit.id for hit in changing),
            remaining=len(left),
            also_matching=left if outcome.beside else (),
            affected_count=len(changing),
            unchanged_count=outcome.unchanged,
            sample=tuple(
                DocumentSample(
                    id=number,
                    title=title,
                    change=outcome.effects[number],
                    web_url=document_link(self._web_url, number),
                )
                for number, title in titled(changing)[:SAMPLE_SIZE]
            ),
            also_changed=self._samples(others, "link mirrored"),
            blocked=self._samples(titled(outcome.blocked), "you may not change it"),
            message=message,
        )


def _needs(operation: Operation, hit: DocumentHit) -> bool:
    """Whether the document is not as asked; also if the operation would be refused for it."""
    try:
        return bool(operation.effects([hit]))
    except Validation:
        return True


def _limited(error: PaperlessMcpError) -> bool:
    return isinstance(error, UpstreamUnavailable) and error.status == HTTPStatus.TOO_MANY_REQUESTS


def _as_asked(total: int) -> str:
    if not total:
        return "no document matches"
    if total == 1:
        return f"the selected document already is {AS_ASKED}"
    return f"all {total} selected documents already are {AS_ASKED}"


def _told(seen: BulkOutcomes, *, running: bool) -> str:
    """What was seen of the documents afterwards, all of them named."""
    states = (
        (AS_ASKED, seen.changed),
        ("not changed", seen.not_changed),
        ("unknown", seen.unknown),
    )
    parts = [f"{state} {', '.join(map(str, ids))}" for state, ids in states if ids]
    return f"Seen after that: {'; '.join(parts)}.{STILL_RUNNING if running else ''}"
