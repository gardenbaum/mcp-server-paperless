"""Listing, acknowledging and starting background tasks (decision-26, decision-93).

Starting a task needs ``confirm`` when it retrains what automatic matching uses or sends every
document to an AI service; a sanity check only reads. Acknowledging a task hides it from the list
of new ones and needs none.
"""

from typing import TYPE_CHECKING, Final, cast

from mcp_server_paperless.domain.errors import ConfirmationRequired, Validation
from mcp_server_paperless.domain.task_lists import (
    Acknowledged,
    StartedTask,
    TaskFilters,
    TaskList,
)
from mcp_server_paperless.services.documents import owner_id

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from mcp_server_paperless.domain.documents import NameOrId
    from mcp_server_paperless.domain.ports import TaskListReader, TaskListWriter
    from mcp_server_paperless.domain.task_lists import (
        RunnableTask,
        TaskOrdering,
        TaskType,
        TriggerSource,
    )
    from mcp_server_paperless.domain.tasks import TaskState
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

TASK_EFFECTS: Final[dict[str, str]] = {
    "train_classifier": (
        "train the classifier again: it learns from the documents' current tags and metadata "
        "and replaces the model that automatic matching uses for new documents"
    ),
    "llm_index": (
        "rebuild the AI index, which sends the text of every document to the embedding service "
        "Paperless is configured with, at that service's cost and for a long time"
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

    async def acknowledge_tasks(self, task_ids: Sequence[int]) -> Acknowledged:
        """Mark tasks (database ids from list_tasks) as seen.

        Raises:
            Validation: An id is listed twice or does not exist.
            PermissionDenied: The user may not change tasks.
        """
        if len(set(task_ids)) != len(task_ids):
            message = "A task id is listed twice."
            raise Validation(message, hint="List every id once.")
        count = await self._writer.acknowledge_tasks(task_ids)
        return Acknowledged(
            acknowledged=count,
            message=f"Acknowledged {_plural(count, 'task')} (they no longer show as new).",
        )

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
