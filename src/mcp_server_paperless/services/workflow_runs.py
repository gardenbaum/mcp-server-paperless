"""Running a workflow by hand on chosen documents (decision-75).

Paperless runs it document by document in a queued task, skips silently what does not match the
filters of the manual triggers, and names in the failed task only the documents an action failed on.
Failed e-mails, webhooks, password removals and AI suggestions fail no task. A run cannot be
repeated safely, so nothing here is, and every unclear end says so.

Residual race: between the last read and the task someone may change the workflow or a document;
Paperless then runs what is stored at that moment.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.bulk import MAX_DOCUMENTS
from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    PaperlessMcpError,
    PermissionDenied,
    TaskFailed,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.workflows import WorkflowRunReport
from mcp_server_paperless.services.bulk_documents import (
    DRY_RUN,
    NEXT_CALL,
    TO_GO_AHEAD,
    TO_GO_AHEAD_BY_IDS,
)
from mcp_server_paperless.services.bulk_operations import named, require_all, titled
from mcp_server_paperless.services.document_edits import COUNTED
from mcp_server_paperless.services.document_runner import refused
from mcp_server_paperless.services.selection import SELECT_BYTES, require_exclusions, unchecked
from mcp_server_paperless.services.taxonomy_edits import documents as count_documents
from mcp_server_paperless.services.workflows import (
    MANUAL,
    TRASH_ACTION,
    TRASH_NOTE,
    UNSEEN_ACTIONS,
    trigger_text,
    unfiltered,
)

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import DocumentHit, NameOrId
    from mcp_server_paperless.domain.ports import DocumentReader, WorkflowWriter
    from mcp_server_paperless.domain.selection import Selection
    from mcp_server_paperless.domain.workflows import Workflow, WorkflowRun
    from mcp_server_paperless.services.selection import DocumentSelector, Selected
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver
    from mcp_server_paperless.services.workflows import WorkflowService

NOT_AGAIN: Final = (
    "What it does may be done already, e-mails and webhooks included: do not run it again on your "
    "own; ask the user what they want."
)
UNSEEN: Final = (
    "Whether e-mails, webhooks, password removals and AI suggestions worked is not told: only the "
    "Paperless logs show it."
)
SKIPPED: Final = (
    ", except those that do not match its manual trigger; Paperless does not tell which"
)


@dataclass(frozen=True, slots=True)
class _Call:
    """What a call would run, on what."""

    workflow: Workflow
    actions: tuple[str, ...]
    apply_filters: bool
    chosen: Selected
    owners: dict[int, int | None]
    blocked: list[DocumentHit]
    # Whether a filter or a view stands alone, and whether one stands with ids.
    alone: bool
    beside: bool

    @property
    def hits(self) -> tuple[DocumentHit, ...]:
        return self.chosen.hits

    @property
    def subject(self) -> str:
        return f"the workflow {quoted(self.workflow.name)} (id {self.workflow.id})"

    @property
    def skips(self) -> bool:
        """Whether documents may be skipped."""
        return self.apply_filters and not unfiltered(self.workflow.triggers)

    @property
    def effect(self) -> str:
        """What confirming would do: every document and every action."""
        manual = "; ".join(trigger_text(t) for t in self.workflow.triggers if t.type == MANUAL)
        if self.skips:
            which = (
                f", skipping those that do not match its manual trigger ({manual}); Paperless does "
                "not tell which it skips"
            )
        elif self.apply_filters:
            which = ", all of which match: its manual trigger has no filters"
        else:
            which = (
                ", each of which counts as matching: the filters of its triggers are not applied"
            )
        will = "; ".join(self.actions)
        return (
            f"run {self.subject} on {named(titled(self.hits))}{which}{unchecked(self.chosen)}. It "
            f"will: {will}"
        )

    @property
    def scope(self) -> object:
        """What the text does not tell apart: the whole workflow and the current values."""
        values = [hit.model_dump(exclude={"highlights", "user_can_change"}) for hit in self.hits]
        return self.workflow, self.apply_filters, values, self.owners

    def more(self, verb: str) -> str:
        """What also matches and is not touched, as a sentence."""
        count = len(self.chosen.others)
        if not count:
            return ""
        noun = "document matches" if count == 1 else "documents match"
        verb = verb if count == 1 else {"is": "are", "was": "were"}[verb]
        return f" {count} other {noun} and {verb} not touched (also_matching)."


class WorkflowRuns:
    """Runs workflows by hand for the current user."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: WorkflowWriter,
        workflows: WorkflowService,
        selector: DocumentSelector,
        taxonomy: TaxonomyResolver,
    ) -> None:
        """Run what ``workflows`` finds on what ``selector`` chooses."""
        self._reader: DocumentReader = reader
        self._writer: WorkflowWriter = writer
        self._workflows: WorkflowService = workflows
        self._selector: DocumentSelector = selector
        self._taxonomy: TaxonomyResolver = taxonomy

    async def run(
        self,
        workflow: NameOrId,
        selection: Selection,
        *,
        apply_filters: bool,
        dry_run: bool,
        confirm: bool,
    ) -> WorkflowRunReport:
        """Preview (``dry_run``) or run ``workflow`` on the documents of ``selection``.

        ``confirm`` confirms only documents that are named by id; a filter or a view alone needs
        an accepted dialog.

        Raises:
            Validation: The workflow is not enabled, has no manual trigger or a destination that
                cannot be shown, or the selection is refused (see ``DocumentSelector.select``) or
                holds more than ``MAX_DOCUMENTS``.
            NotFound: The workflow, a document, a view or a name is unknown or hidden.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not view workflows, or not run one on a document.
            ConfirmationRequired: It is not confirmed.
            TaskFailed: It failed on every document, or it is not known on which.
            PaperlessMcpError: The answer was lost; what ran is not known.
        """
        call = await self._call(workflow, selection, apply_filters=apply_filters)
        if not call.hits:
            return _report(call, "Nothing was run: no document matches.")
        if dry_run:
            return _preview(call)
        if call.blocked:
            message = (
                f"You may not change or do not own {named(titled(call.blocked))}, so Paperless "
                "would refuse the whole run; nothing was run."
            )
            hint = (
                "Leave those documents out: a workflow runs by hand only on documents you own, or "
                "that have no owner."
            )
            raise PermissionDenied(message, hint=hint)
        if call.alone or not confirm:
            await self._require(call)
        return _done(call, await self._send(call))

    async def _call(
        self, workflow: NameOrId, selection: Selection, *, apply_filters: bool
    ) -> _Call:
        by_filter = selection.filters is not None or selection.saved_view is not None
        ids = dict.fromkeys(selection.document_ids)
        if len(ids) > MAX_DOCUMENTS:
            message = f"Select between 1 and {MAX_DOCUMENTS} documents per call."
            raise Validation(message, hint="Work in several calls.")
        found = await self._workflows.find(workflow)
        _require_usable(found)
        actions = await self._workflows.told(found)
        chosen = await self._selector.select(selection, change=True)
        require_all(chosen.missing)
        require_exclusions(chosen)
        if len(chosen.hits) > MAX_DOCUMENTS:
            message = (
                f"{len(chosen.hits)} documents match, and a workflow that ran leaves them "
                f"matching: at most {MAX_DOCUMENTS} may be chosen by filters or saved_view."
            )
            hint = "Narrow the filters, or add document_ids: read_documents shows them."
            raise Validation(message, hint=hint)
        owners = await self._owners(chosen)
        return _Call(
            workflow=found,
            actions=actions,
            apply_filters=apply_filters,
            chosen=chosen,
            owners=owners,
            blocked=await self._blocked(chosen, owners),
            alone=by_filter and not ids,
            beside=by_filter and bool(ids),
        )

    async def _owners(self, chosen: Selected) -> dict[int, int | None]:
        if not chosen.hits:
            return {}
        rows = await self._reader.read_rows(
            [hit.id for hit in chosen.hits], ["owner"], max_bytes=SELECT_BYTES
        )
        return {row.id: row.owner for row in rows}

    async def _blocked(self, chosen: Selected, owners: dict[int, int | None]) -> list[DocumentHit]:
        """The documents Paperless would refuse, as far as that can be known."""
        user = await self._reader.get_signed_in() if any(owners.values()) else None
        foreign = {
            number
            for number, owner in owners.items()
            if user is not None and not user.is_superuser and owner not in {None, user.id}
        }
        return [h for h in chosen.hits if h.user_can_change is False or h.id in foreign]

    async def _require(self, call: _Call) -> None:
        """Refuse, unless the user accepted exactly this.

        Raises:
            ConfirmationRequired: They did not; the hint names the next call.
        """
        ids = [hit.id for hit in call.hits]
        files = await self._reader.get_newest_versions(ids)
        listed = ", ".join(map(str, ids))
        hint = NEXT_CALL.format(ids=listed, what="workflow and apply_filters")
        ConfirmationRequired.require(
            call.effect,
            scope=(call.scope, sorted(files.items())),
            hint=hint if call.alone else CONFIRM_HINT,
        )

    async def _send(self, call: _Call) -> WorkflowRun:
        """Run it; a failure after which it is not known what ran says so."""
        try:
            return await self._writer.run_workflow(
                call.workflow.id,
                [hit.id for hit in call.hits],
                filter_documents=call.apply_filters,
            )
        except PaperlessMcpError as error:
            if refused(error) and not isinstance(error, TaskFailed):
                raise
            error.message = (
                f"{error.message} On which of {named(titled(call.hits))} {call.subject} ran is "
                "unknown."
            )
            error.hint = NOT_AGAIN
            raise
        finally:
            for kind in COUNTED:
                self._taxonomy.invalidate(kind)


def _require_usable(workflow: Workflow) -> None:
    subject = f"Nothing was run: the workflow {quoted(workflow.name)} (id {workflow.id})"
    if not workflow.enabled:
        message = f"{subject} is not enabled, so Paperless does not run it."
        hint = "If the user wants that, switch it on with update_workflow first."
        raise Validation(message, hint=hint)
    if not any(trigger.type == MANUAL for trigger in workflow.triggers):
        message = f"{subject} has no manual trigger, so it cannot be run by hand."
        raise Validation(message, hint="Add a manual trigger to it in the Paperless web UI.")


def _preview(call: _Call) -> WorkflowRunReport:
    if call.blocked:
        message = (
            f"{DRY_RUN} The run would be refused as a whole: you may not change or do not own "
            f"{named(titled(call.blocked))}."
        )
    else:
        ahead = TO_GO_AHEAD_BY_IDS if call.alone else TO_GO_AHEAD
        message = f"{DRY_RUN} Confirming would {call.effect}. {ahead}{call.more('is')}"
    return _report(call, message, dry_run=True)


def _done(call: _Call, run: WorkflowRun) -> WorkflowRunReport:
    """The report of a run.

    Raises:
        TaskFailed: It failed on every document.
    """
    docs = count_documents(len(call.hits))
    if run.unconfirmed is not None:
        message = (
            f"Asked Paperless to run {call.subject} on {docs}. Not confirmed: Paperless queued "
            f"task {run.unconfirmed.task_id}, but {run.unconfirmed.reason}. Read it with get_task. "
            f"{NOT_AGAIN}{call.more('was')}"
        )
        task = run.unconfirmed.task_id
        return _report(call, message, started=True, unconfirmed_task_id=task)
    skipped = SKIPPED if call.skips else ""
    types = {action.type for action in call.workflow.actions}
    notes = f" {UNSEEN}" if types & UNSEEN_ACTIONS else ""
    notes += f" {TRASH_NOTE}" if TRASH_ACTION in types else ""
    notes += call.more("was")
    if not run.failed:
        return _report(call, f"Ran {call.subject} on {docs}{skipped}.{notes}", started=True)
    message = (
        f"T{call.subject[1:]} failed on {len(run.failed)} of {docs}: "
        f"{', '.join(map(str, run.failed))}. Some of its actions may have run there; the Paperless "
        "logs show the reason."
    )
    if len(run.failed) == len(call.hits):
        raise TaskFailed(message, hint=NOT_AGAIN)
    message += f" It ran on the others{skipped}. {NOT_AGAIN}{notes}"
    return _report(call, message, started=True, failed=run.failed)


def _report(  # noqa: PLR0913 -- one keyword per way a call ends
    call: _Call,
    message: str,
    *,
    dry_run: bool = False,
    started: bool = False,
    unconfirmed_task_id: str | None = None,
    failed: tuple[int, ...] = (),
) -> WorkflowRunReport:
    return WorkflowRunReport(
        workflow_id=call.workflow.id,
        workflow=call.workflow.name,
        actions=call.actions,
        apply_filters=call.apply_filters,
        dry_run=dry_run,
        started=started,
        unconfirmed_task_id=unconfirmed_task_id,
        matched=call.chosen.matched,
        selected_ids=tuple(hit.id for hit in call.hits),
        also_matching=call.chosen.also_matching if call.beside else (),
        failed=failed,
        blocked=tuple(hit.id for hit in call.blocked),
        message=message,
    )
