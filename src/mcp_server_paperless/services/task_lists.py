"""Listing, acknowledging and starting background tasks (decision-26, decision-93).

Starting a task needs ``confirm`` when it retrains what automatic matching uses or sends every
document to an AI service; a sanity check only reads. Acknowledging a task hides it from the list
of new ones and needs none; acknowledging all of them needs one (decision-94).
"""

import asyncio
from datetime import UTC
from typing import TYPE_CHECKING, Final, Literal, cast

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    PaperlessMcpError,
    PermissionDenied,
    UnexpectedAnswer,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.task_lists import (
    Acknowledged,
    StartedTask,
    TaskFilters,
    TaskList,
    TaskOverview,
    TaskViewer,
)
from mcp_server_paperless.services.documents import owner_id

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence
    from datetime import datetime

    from mcp_server_paperless.domain.documents import NameOrId
    from mcp_server_paperless.domain.ports import TaskListReader, TaskListWriter
    from mcp_server_paperless.domain.task_lists import (
        RunnableTask,
        TaskCounts,
        TaskOrdering,
        TaskRow,
        TaskType,
        TriggerSource,
    )
    from mcp_server_paperless.domain.tasks import TaskState
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# The tasks acknowledging all is about.
NEW: Final = TaskFilters(acknowledged=False)
NOTHING_CHANGED: Final = "Nothing was changed."
# Failures a preview names; the rest are counted.
SHOWN_FAILURES: Final = 10
# Paperless silently cuts a longer summary to this.
MAX_DAYS: Final = 365
TASK_EFFECTS: Final[dict[str, str]] = {
    "train_classifier": (
        "train the classifier again: it learns from the documents' current tags and metadata "
        "and replaces the model that automatic matching uses for new documents"
    ),
    "llm_index": (
        "update the AI index, which sends the text, notes and custom field values of every "
        "document that is new or changed since its last update (of every document if there is no "
        "index yet or the embedding model changed) to the embedding service Paperless is "
        "configured with, at that service's cost and possibly for a long time; it does nothing "
        "while the index is off"
    ),
}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _several[T: str](field: str, given: T | Sequence[T] | None) -> tuple[T, ...]:
    """One value or several as several; an empty list would be no filter at all."""
    if given is None:
        return ()
    if isinstance(given, str):
        return (cast("T", given),)
    if not given:
        message = f"{field} must name at least one value."
        raise Validation(message, hint=f"Leave {field} out for all.")
    return tuple(given)


def _check_text(field: str, value: str | None) -> None:
    """Paperless ignores a blank text and would answer every task."""
    if value is not None and not value.strip():
        message = f"{field} must not be blank."
        raise Validation(message, hint=f"Give some text, or leave {field} out.")


def _done(count: int, more: int = 0) -> Acknowledged:
    """The result; ``more`` is how many came in between preview and call."""
    late = f" {more} of them came in after the preview." if more > 0 else ""
    message = f"Acknowledged {_plural(count, 'task')} (they no longer show as new).{late}"
    return Acknowledged(acknowledged=count, dry_run=False, message=message)


def _named(row: TaskRow) -> str:
    """A task in a confirmation: type, file, id and time."""
    file = f" {quoted(row.file_name)}" if row.file_name else ""
    at = row.created.astimezone(UTC)
    return f"{row.task_type}{file} (id {row.id}, {at:%Y-%m-%d %H:%M} UTC)"


async def _unless_denied[T](call: Awaitable[T]) -> T | PermissionDenied:
    """What the call answers, or the refusal."""
    try:
        return await call
    except PermissionDenied as error:
        return error


class TaskListService:
    """Lists, acknowledges and starts background tasks."""

    def __init__(
        self, reader: TaskListReader, writer: TaskListWriter, taxonomy: TaxonomyResolver
    ) -> None:
        """Read and write through the ports; resolve user names with ``taxonomy``."""
        self._reader: TaskListReader = reader
        self._writer: TaskListWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy

    async def list_tasks(  # noqa: PLR0913 -- one parameter per filter
        self,
        *,
        state: TaskState | Sequence[TaskState] | None = None,
        task_type: TaskType | Sequence[TaskType] | None = None,
        trigger_source: TriggerSource | Sequence[TriggerSource] | None = None,
        acknowledged: bool | None = None,
        is_complete: bool | None = None,
        owner: NameOrId | None = None,
        name: str | None = None,
        result: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        ordering: TaskOrdering = "-created",
        page: int = 1,
        page_size: int = 25,
    ) -> TaskList:
        """One page of the tasks that match every filter.

        Raises:
            NotFound: ``page`` is past the last page, or no user has the name ``owner``.
            Validation: A filter is empty, or Paperless knows no user with the id ``owner``.
            PermissionDenied: The user may not view tasks, or gave a user name and may not list
                users.
        """
        _check_text("name", name)
        _check_text("result", result)
        filters = TaskFilters(
            states=_several("state", state),
            task_types=_several("task_type", task_type),
            trigger_sources=_several("trigger_source", trigger_source),
            acknowledged=acknowledged,
            is_complete=is_complete,
            owner=await owner_id(self._taxonomy, owner),
            name=name,
            result=result,
            created_from=created_from,
            created_to=created_to,
        )
        found = await self._reader.list_tasks(
            filters, ordering=ordering, page=page, page_size=page_size
        )
        return TaskList(count=found.count, page=page, next_page=found.next_page, tasks=found.rows)

    async def overview(  # noqa: PLR0913 -- one parameter per filter
        self,
        *,
        days: int = 30,
        acknowledged: bool | None = None,
        task_type: TaskType | Sequence[TaskType] | None = None,
        trigger_source: TriggerSource | Sequence[TriggerSource] | None = None,
        owner: NameOrId | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        name: str | None = None,
        result: str | None = None,
    ) -> TaskOverview:
        """Counts (of what matches the filters), running tasks, and the last ``days`` by type.

        Raises:
            NotFound: No user has the name ``owner``.
            Validation: A filter is empty, ``days`` is out of range, or Paperless knows no user
                with the id ``owner``.
            PermissionDenied: The user may see no part, or gave a user name and may not list users.
        """
        if not 1 <= days <= MAX_DAYS:
            message = f"days must be between 1 and {MAX_DAYS}."
            raise Validation(message, hint="The default is 30.")
        _check_text("name", name)
        _check_text("result", result)
        filters = TaskFilters(
            task_types=_several("task_type", task_type),
            trigger_sources=_several("trigger_source", trigger_source),
            acknowledged=acknowledged,
            owner=await owner_id(self._taxonomy, owner),
            name=name,
            result=result,
            created_from=created_from,
            created_to=created_to,
        )
        counts, active, by_type, viewer = await asyncio.gather(
            _unless_denied(self._reader.count_tasks(filters)),
            _unless_denied(self._reader.list_active_tasks()),
            _unless_denied(self._reader.summarize_tasks(days=days)),
            _unless_denied(self._reader.get_viewer()),
        )
        parts = {"counts": counts, "active": active, "by_type": by_type}
        refused = [part for part in parts.values() if isinstance(part, PermissionDenied)]
        if len(refused) == len(parts):
            raise refused[0]
        of: Literal["every_user", "own_and_unowned", "unknown"] = "unknown"
        if isinstance(viewer, TaskViewer):
            of = "every_user" if viewer.summary_of_all else "own_and_unowned"
        return TaskOverview(
            counts=None if isinstance(counts, PermissionDenied) else counts,
            active=None if isinstance(active, PermissionDenied) else active,
            by_type=None if isinstance(by_type, PermissionDenied) else by_type,
            days=days,
            by_type_of=of,
            unavailable=tuple(n for n, p in parts.items() if isinstance(p, PermissionDenied)),
        )

    async def acknowledge_tasks(
        self,
        task_ids: Sequence[int] | None = None,
        *,
        all_unacknowledged: bool = False,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> Acknowledged:
        """Mark tasks as seen: these (database ids), or with ``confirm`` every new one.

        Raises:
            ConfirmationRequired: All of them, and neither ``dry_run`` nor ``confirm``.
            Validation: Not exactly one of both, or an id is listed twice or does not exist.
            PermissionDenied: The user may not change tasks, or view them for the preview.
        """
        if (task_ids is None) is not all_unacknowledged:
            message = "Give either task_ids or all_unacknowledged=true."
            raise Validation(message, hint="Ids are in list_tasks (the id field).")
        if task_ids is None:
            return await self._acknowledge_all(dry_run=dry_run, confirm=confirm)
        if len(set(task_ids)) != len(task_ids):
            message = "A task id is listed twice."
            raise Validation(message, hint="List every id once.")
        if dry_run:
            message = f"{NOTHING_CHANGED} This would mark {_plural(len(task_ids), 'task')} as seen."
            return Acknowledged(acknowledged=0, dry_run=True, message=message)
        return _done(await self._writer.acknowledge_tasks(task_ids))

    async def _acknowledge_all(self, *, dry_run: bool, confirm: bool) -> Acknowledged:
        before = await self._reader.count_tasks(NEW)
        if not before.total:
            message = "Nothing to do: every task you may see was acknowledged before."
            return Acknowledged(acknowledged=0, dry_run=dry_run, message=message)
        if dry_run or not confirm:
            effect = await self._effect(before)
            if dry_run:
                message = f"{NOTHING_CHANGED} This would {effect}."
                return Acknowledged(acknowledged=0, dry_run=True, message=message)
            newest = await self._reader.list_tasks(NEW, page=1, page_size=1)
            ConfirmationRequired.require(effect, scope=(before, [row.id for row in newest.rows]))
        try:
            count = await self._writer.acknowledge_all_tasks()
        except UpstreamUnreachable:
            raise
        except UpstreamUnavailable, UnexpectedAnswer:
            return await self._looked_up(before)
        return _done(count, more=count - before.total)

    async def _effect(self, before: TaskCounts) -> str:
        """What acknowledging all does, with the newest failures and whose tasks are among them."""
        failures = await self._reader.list_tasks(
            NEW.model_copy(update={"states": ("failure", "revoked")}),
            page=1,
            page_size=SHOWN_FAILURES,
        )
        lead = "the only task" if before.total == 1 else f"all {before.total} tasks"
        text = (
            f"mark {lead} nobody acknowledged yet as seen, which cannot be taken back: "
            f"{before.needs_attention} failed or cancelled, {before.in_progress} waiting or "
            f"running, {before.completed} completed{await self._others(before)}"
        )
        if failures.rows:
            named = ", ".join(_named(row) for row in failures.rows)
            rest = failures.count - len(failures.rows)
            more = f" and {rest} more" if rest > 0 else ""
            text += f"; the newest failures: {named}{more}"
        return f"{text}. Tasks that come in until it is done are marked, too"

    async def _others(self, before: TaskCounts) -> str:
        """How many of the tasks are not the user's own."""
        viewer = await _unless_denied(self._reader.get_viewer())
        if isinstance(viewer, PermissionDenied):
            return "; whether tasks of others are among them is not known"
        own = await self._reader.count_tasks(NEW.model_copy(update={"owner": viewer.id}))
        others = before.total - own.total
        if others <= 0:
            return ""
        if viewer.sees_all:
            verb = "is" if others == 1 else "are"
            return f"; {others} of them {verb} not yours (another user's or nobody's)"
        return f"; {others} of them {'belongs' if others == 1 else 'belong'} to nobody"

    async def _looked_up(self, before: TaskCounts) -> Acknowledged:
        """What became of a call whose answer was lost, read once."""
        lost, then = "Paperless's answer was lost", f"({before.total} at the preview)"
        unknown = "so it is not known whether it was done. Do not call again on your own"
        try:
            left = (await self._reader.count_tasks(NEW)).total
        except PaperlessMcpError:
            message = f"{lost} and the tasks cannot be read now, {unknown}: look at list_tasks."
        else:
            now = f"{lost}; {_plural(left, 'task')} {'is' if left == 1 else 'are'} new now {then}"
            done = f"{lost}; no task is new any more, so it was done {then}."
            message = f"{now}, {unknown}: look at list_tasks." if left else done
        return Acknowledged(acknowledged=None, dry_run=False, message=message)

    async def run_task(self, task_type: RunnableTask, *, confirm: bool = False) -> StartedTask:
        """Start a task by hand; training the classifier and the AI index need ``confirm``.

        Raises:
            ConfirmationRequired: The task needs ``confirm`` and it is not set.
            PermissionDenied: Only a superuser may start tasks.
        """
        if task_type in TASK_EFFECTS and not confirm:
            # scope: the task is the argument; it has no other target.
            ConfirmationRequired.require(TASK_EFFECTS[task_type], scope=None)
        task_id = await self._writer.run_task(task_type)
        return StartedTask(
            task_id=task_id,
            task_type=task_type,
            message=f"Started {task_type}; follow it with get_task(task_id).",
        )
