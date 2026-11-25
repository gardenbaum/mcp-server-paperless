"""TaskListReader and TaskListWriter on ``/api/tasks/`` (Paperless 3.5.1).

- The list is paginated (v10). Staff users see every task, others their own and those without an
  owner. ``status``, ``task_type`` and ``trigger_source`` can be given several times.
- ``input_data`` of an upload holds its file name, a server path and the metadata asked for: only
  the file name is read (decision-93).
- ``status_counts`` takes the filters of the list but ``status`` and ``is_complete``, although its
  schema declares none; ``active`` and ``summary`` declare all and ignore them. ``summary`` counts
  every user's tasks for who may view the system status, also tasks the list does not show.
- ``acknowledge`` takes database ids and refuses ids that do not exist (400), or ``all``: every new
  task the user may see at that moment. There is no way back. ``run`` is for
  superusers only and starts ``train_classifier``, ``sanity_check`` or ``llm_index``; any other
  type is a 400. Its answer is the Celery id, not the database id.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, JsonValue, RootModel

from mcp_server_paperless.domain.errors import NotFound
from mcp_server_paperless.domain.task_lists import (
    TaskCounts,
    TaskRow,
    TaskRowPage,
    TaskTypeSummary,
    TaskViewer,
)
from mcp_server_paperless.domain.tasks import REJECTED, TaskState
from mcp_server_paperless.domain.urls import text_without_credentials
from mcp_server_paperless.paperless.admin import UI_SETTINGS, Refused
from mcp_server_paperless.paperless.pagination import Page
from mcp_server_paperless.paperless.tasks import duplicate_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.task_lists import RunnableTask, TaskFilters, TaskOrdering
    from mcp_server_paperless.paperless.client import PaperlessClient

TASKS: Final = "/api/tasks/"
# The first line of a failed task's message: later lines can be a traceback with server paths.
MAX_MESSAGE: Final = 300
# What Paperless calls the fields of a row it can sort by, where the name differs.
COLUMNS: Final = {"created": "date_created", "finished": "date_done", "state": "status"}

VIEW_TASKS: Final = (
    "view tasks",
    "It needs the view_paperlesstask permission, or must be a superuser.",
)
ACK_TASKS: Final = (
    "acknowledge tasks",
    "It needs the change_paperlesstask permission, or must be a superuser.",
)
RUN_TASKS: Final = ("start tasks", "Only a superuser may start tasks.")


def beyond_last_page(page: int) -> NotFound:
    """The error for a page past the end of the tasks."""
    return NotFound(
        f"Page {page} of the tasks does not exist.",
        hint="List the tasks with page=1; count and next_page tell how far it goes.",
    )


class _Task(BaseModel):
    id: int
    task_id: str
    task_type: str
    trigger_source: str
    status: TaskState
    date_created: datetime
    date_started: datetime | None
    date_done: datetime | None
    duration_seconds: float | None
    wait_time_seconds: float | None
    input_data: JsonValue
    result_data: JsonValue
    related_document_ids: list[int]
    acknowledged: bool
    owner: int | None


class _Tasks(RootModel[list[_Task]]):
    pass


class _Counts(BaseModel):
    all: int
    needs_attention: int
    in_progress: int
    completed: int


class _TypeSummary(BaseModel):
    task_type: str
    total_count: int
    pending_count: int
    success_count: int
    failure_count: int
    avg_duration_seconds: float | None
    avg_wait_time_seconds: float | None
    last_run: datetime | None
    last_success: datetime | None
    last_failure: datetime | None


class _Summary(RootModel[list[_TypeSummary]]):
    pass


class _Me(BaseModel):
    id: int
    is_staff: bool
    is_superuser: bool


class _UiSettings(BaseModel):
    user: _Me
    permissions: list[str]


class _Acknowledged(BaseModel):
    result: int


class _Started(BaseModel):
    task_id: str


def _message(result: JsonValue) -> str | None:
    """The first line of the error message of a failed task, cut short."""
    if not isinstance(result, dict):
        return None
    message = result.get("error_message")
    if not isinstance(message, str):
        return None
    lines = [
        line.strip() for line in text_without_credentials(message).splitlines() if line.strip()
    ]
    return lines[0][:MAX_MESSAGE] if lines else None


def _file_name(given: JsonValue) -> str | None:
    """The file of an upload. The rest of what a task was given holds paths and metadata."""
    name = given.get("filename") if isinstance(given, dict) else None
    return name if isinstance(name, str) and name else None


def _task(row: _Task) -> TaskRow:
    rejected = duplicate_of(row.result_data) is not None
    return TaskRow(
        id=row.id,
        task_id=row.task_id,
        task_type=row.task_type,
        trigger_source=row.trigger_source,
        state=row.status,
        created=row.date_created,
        started=row.date_started,
        finished=row.date_done,
        duration_seconds=row.duration_seconds,
        wait_time_seconds=row.wait_time_seconds,
        file_name=_file_name(row.input_data),
        owner=row.owner,
        document_ids=() if rejected else tuple(row.related_document_ids),
        acknowledged=row.acknowledged,
        message=REJECTED if rejected else _message(row.result_data),
    )


def _query(filters: TaskFilters) -> dict[str, str | int | list[str]]:
    """The query of the filters; a list repeats its name."""
    flags = {"acknowledged": filters.acknowledged, "is_complete": filters.is_complete}
    moments = {
        "date_created_after": filters.created_from,
        "date_created_before": filters.created_to,
    }
    given: dict[str, str | int | list[str] | None] = {
        "status": list(filters.states),
        "task_type": list(filters.task_types),
        "trigger_source": list(filters.trigger_sources),
        **{name: None if flag is None else str(flag).lower() for name, flag in flags.items()},
        "owner": filters.owner,
        "name": filters.name,
        "result": filters.result,
        **{name: None if at is None else at.isoformat() for name, at in moments.items()},
    }
    return {name: value for name, value in given.items() if value is not None}


def _ordering(ordering: TaskOrdering) -> str:
    """Paperless's name of the field, then the newest first, which keeps pages apart."""
    sign, field = ("-", ordering[1:]) if ordering.startswith("-") else ("", ordering)
    column = COLUMNS.get(field, field)
    return f"{sign}{column}" if field == "created" else f"{sign}{column},-date_created"


class PaperlessTaskListReader:
    """``TaskListReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def list_tasks(
        self,
        filters: TaskFilters,
        *,
        ordering: TaskOrdering = "-created",
        page: int,
        page_size: int,
    ) -> TaskRowPage:
        """See ``TaskListReader.list_tasks``."""
        params = {
            **_query(filters),
            "page": page,
            "page_size": page_size,
            "ordering": _ordering(ordering),
        }
        with Refused(VIEW_TASKS):
            try:
                found = await self._client.get(TASKS, Page[_Task], params=params)
            except NotFound:
                raise beyond_last_page(page) from None
        return TaskRowPage(
            count=found.count,
            next_page=found.next_page,
            rows=tuple(_task(row) for row in found.results),
        )

    async def count_tasks(self, filters: TaskFilters) -> TaskCounts:
        """See ``TaskListReader.count_tasks``."""
        with Refused(VIEW_TASKS):
            found = await self._client.get(
                f"{TASKS}status_counts/", _Counts, params=_query(filters)
            )
        return TaskCounts(
            total=found.all,
            needs_attention=found.needs_attention,
            in_progress=found.in_progress,
            completed=found.completed,
        )

    async def list_active_tasks(self) -> tuple[TaskRow, ...]:
        """See ``TaskListReader.list_active_tasks``."""
        with Refused(VIEW_TASKS):
            found = await self._client.get(f"{TASKS}active/", _Tasks)
        return tuple(_task(row) for row in found.root)

    async def summarize_tasks(self, *, days: int) -> tuple[TaskTypeSummary, ...]:
        """See ``TaskListReader.summarize_tasks``."""
        with Refused(VIEW_TASKS):
            found = await self._client.get(f"{TASKS}summary/", _Summary, params={"days": days})
        return tuple(
            TaskTypeSummary(
                task_type=row.task_type,
                total=row.total_count,
                pending=row.pending_count,
                success=row.success_count,
                failure=row.failure_count,
                avg_duration_seconds=row.avg_duration_seconds,
                avg_wait_time_seconds=row.avg_wait_time_seconds,
                last_run=row.last_run,
                last_success=row.last_success,
                last_failure=row.last_failure,
            )
            for row in found.root
        )

    async def get_viewer(self) -> TaskViewer:
        """See ``TaskListReader.get_viewer``: the ui settings, which carry user and permissions."""
        found = await self._client.get(UI_SETTINGS, _UiSettings)
        user = found.user
        status = user.is_superuser or "view_system_monitoring" in found.permissions
        return TaskViewer(
            id=user.id, sees_all=user.is_staff, summary_of_all=user.is_staff or status
        )


class PaperlessTaskListWriter:
    """``TaskListWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def acknowledge_tasks(self, task_ids: Sequence[int]) -> int:
        """See ``TaskListWriter.acknowledge_tasks``."""
        with Refused(ACK_TASKS):
            done = await self._client.call(
                "POST",
                f"{TASKS}acknowledge/",
                _Acknowledged,
                json={"tasks": list(task_ids), "all": False},
            )
        return done.result

    async def acknowledge_all_tasks(self) -> int:
        """See ``TaskListWriter.acknowledge_all_tasks``: a POST, which the client never retries."""
        with Refused(ACK_TASKS):
            done = await self._client.call(
                "POST", f"{TASKS}acknowledge/", _Acknowledged, json={"all": True}
            )
        return done.result

    async def run_task(self, task_type: RunnableTask) -> str:
        """See ``TaskListWriter.run_task``: a POST, which the client never retries."""
        with Refused(RUN_TASKS):
            started = await self._client.call(
                "POST", f"{TASKS}run/", _Started, json={"task_type": task_type}
            )
        return started.task_id
