"""Paperless background tasks: uploads and PDF operations run as tasks, and clients follow them.

Task ids are the Celery UUIDs Paperless returns when it accepts work, not database ids.
"""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.duplicates import DuplicateReport

type TaskState = Literal["pending", "started", "success", "failure", "revoked"]

# The states a task never leaves.
FINAL_STATES: Final[frozenset[TaskState]] = frozenset({"success", "failure", "revoked"})

# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class Unconfirmed(BaseModel):
    """A change Paperless queued as a task whose result could not be observed.

    Attributes:
        task_id: The queued task; ``get_task`` reads it.
        reason: Why it could not be followed to its end, as the rest of a sentence.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: str
    reason: str


# What a task says that Paperless ended because it already holds the file; which document that is
# is told only to a user who may see it.
REJECTED: Final = "Paperless rejected the file: it already holds the same file."


class _Task(BaseModel):
    """What the port and the tool both say of a task."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: str
    task_type: Annotated[str, Field(description="E.g. consume_file (an upload).")]
    state: Annotated[TaskState, Field(description="pending, started, success, failure or revoked.")]
    message: Annotated[str | None, Field(description="Why the task failed; null otherwise.")]
    document_ids: Annotated[
        tuple[int, ...],
        Field(description="The documents the task created or changed, once known; may be several."),
    ]
    created: datetime
    finished: Annotated[datetime | None, Field(description="Null while the task is running.")]

    @property
    def is_final(self) -> bool:
        """Whether the task has ended, successfully or not."""
        return self.state in FINAL_STATES


class TaskStatus(_Task):
    """A Paperless task as the port reads it.

    Attributes:
        duplicate_of: The document or version Paperless rejected the file for; the user may not be
            allowed to know it.
    """

    duplicate_of: int | None = None


type Wait = Literal["not_requested", "finished", "timed_out"]


class TaskReport(_Task):
    """A task, and what waiting for it did."""

    duplicates: Annotated[
        DuplicateReport | None,
        Field(description="For a finished upload: the same file elsewhere; null otherwise."),
    ] = None

    wait: Annotated[
        Wait,
        Field(
            description=(
                "not_requested: wait_seconds was 0, so the task was read once; finished: the task "
                "ended within the wait (see state); timed_out: still running after "
                "waited_seconds, ask again."
            )
        ),
    ]
    waited_seconds: float
