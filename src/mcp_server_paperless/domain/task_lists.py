"""The list of background tasks: rows, filters, and starting and acknowledging tasks.

One task by its Celery id is ``domain.tasks``. Rows here carry the database id that acknowledging
needs, and of what a task was given only the file name (decision-93).
"""

from datetime import datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.tasks import TaskState

type TaskType = Literal[
    "consume_file",
    "train_classifier",
    "sanity_check",
    "index_optimize",
    "mail_fetch",
    "llm_index",
    "empty_trash",
    "check_workflows",
    "bulk_update",
    "reprocess_document",
    "build_share_link",
    "bulk_delete",
    "apply_ai_suggestions",
]
# The tasks Paperless lets a superuser start by hand (TasksViewSet._RUNNABLE_TASKS).
type RunnableTask = Literal["train_classifier", "sanity_check", "llm_index"]
type TriggerSource = Literal[
    "scheduled",
    "web_ui",
    "api_upload",
    "folder_consume",
    "email_consume",
    "system",
    "manual",
]
# The fields of a row, ``-`` for descending.
type TaskOrdering = Literal[
    "-created",
    "created",
    "-finished",
    "finished",
    "-state",
    "state",
    "-task_type",
    "task_type",
    "-duration_seconds",
    "duration_seconds",
    "-wait_time_seconds",
    "wait_time_seconds",
]


class TaskFilters(BaseModel):
    """Which tasks to list; a field left empty does not filter, several values mean any of them."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    states: tuple[TaskState, ...] = ()
    task_types: tuple[TaskType, ...] = ()
    trigger_sources: tuple[TriggerSource, ...] = ()
    acknowledged: bool | None = None
    is_complete: bool | None = None
    owner: int | None = None
    name: str | None = None
    result: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


class TaskRow(BaseModel):
    """A background task with the database id that acknowledging needs."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: Annotated[int, Field(description="Database id: what acknowledge_tasks takes.")]
    task_id: Annotated[str, Field(description="Celery UUID: what get_task takes.")]
    task_type: str
    trigger_source: Annotated[
        str, Field(description="What started it: the values of the argument of the same name.")
    ]
    file_name: Annotated[str | None, Field(description="The uploaded file; null for other types.")]
    owner: Annotated[int | None, Field(description="User id; null: visible to all.")]
    state: Annotated[TaskState, Field(description="pending, started, success, failure or revoked.")]
    created: datetime
    started: datetime | None
    finished: Annotated[datetime | None, Field(description="Null while the task is running.")]
    wait_time_seconds: Annotated[float | None, Field(description="From creation to start.")]
    duration_seconds: float | None
    document_ids: Annotated[tuple[int, ...], Field(description="Documents the task touched.")]
    acknowledged: bool
    message: Annotated[str | None, Field(description="Why the task failed; null otherwise.")]


class TaskRowPage(BaseModel):
    """One page of tasks as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    rows: tuple[TaskRow, ...]


class TaskList(BaseModel):
    """One page of tasks, newest first."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many tasks match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    tasks: tuple[TaskRow, ...]


class Acknowledged(BaseModel):
    """How many tasks were acknowledged."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    acknowledged: int
    message: str


class StartedTask(BaseModel):
    """A task that was started by hand."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: Annotated[str, Field(description="Follow it with get_task.")]
    task_type: RunnableTask
    message: str
