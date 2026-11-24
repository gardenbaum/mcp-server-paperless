"""Waiting for the work Paperless queues behind the document operations.

The document operations (``bulk_edit``, ``delete``, ``reprocess``, ``rotate``, ``merge``,
``merge_as_versions``, ``edit_pdf`` and ``remove_password``) only queue a ``bulk_update`` task and
answer ``{"result": "OK", "task_id": ...}``: the change is made some milliseconds later, and a
failure shows only in the task.

``TaskWaiter`` makes the adapter keep the meaning "done when the call returns": it follows the
task named in the answer until it ends. An answer without a task id does not match the schema
and is refused as an unexpected answer.

Where the end cannot be observed the outcome is ``Unconfirmed`` (the task id and why), never a
silent success: a user who may not view tasks (``view_paperlesstask``), a task that never shows
up in the list, a task list that stays unreadable until the deadline, or a task still running
at the deadline. The request was accepted, so no error is raised that would invite a repeat; the
caller reports the change as not confirmed. A user who cannot follow the task is first given a
fixed grace to let the worker make the change (a best effort). Transient read errors, also
after the task was seen, are waited out.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel

from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    TaskFailed,
    Validation,
)
from mcp_server_paperless.domain.tasks import TaskState, Unconfirmed
from mcp_server_paperless.paperless.tasks import PaperlessTaskReader

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp_server_paperless.paperless.client import PaperlessClient

# How long the task of a request may run, how often it is read meanwhile, and how long its row
# may be missing before it is taken to be invisible to this user.
WAIT_SECONDS: Final = 60.0
POLL_SECONDS: Final = 0.2
VISIBLE_SECONDS: Final = 3.0
# What a task that cannot be followed is given to run, in place of following it.
UNOBSERVED_SECONDS: Final = 2.0


class Queued(BaseModel):
    """The answer of a document operation: the task it queued."""

    result: str
    task_id: str


FAILED_HINT: Final = (
    "Paperless may have applied part of it before it failed; read the documents again before "
    "repeating it, and check the Paperless logs for the reason."
)
FORBIDDEN_REASON: Final = "the Paperless user may not view tasks, so it cannot be followed"
INVISIBLE_REASON: Final = "it did not show up in the task list"
UNREADABLE_REASON: Final = "the task list could not be read"


def not_applied(what: str) -> TaskFailed:
    """The error for a queued ``what`` whose task failed."""
    return TaskFailed(f"Paperless could not apply the {what}: its task failed.", hint=FAILED_HINT)


def task_failed(task_id: str, causes: str) -> TaskFailed:
    """The error for a failed task whose cause Paperless does not name; ``causes`` are likely."""
    return TaskFailed(
        f"Paperless reported task {task_id} as failed; the likely causes are {causes}.",
        hint=FAILED_HINT,
    )


class TaskWaiter:
    """Follows the task a document operation queued until it has ended."""

    def __init__(  # noqa: PLR0913 -- one knob per bound of the wait
        self,
        client: PaperlessClient,
        *,
        wait_seconds: float = WAIT_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        visible_seconds: float = VISIBLE_SECONDS,
        unobserved_seconds: float = UNOBSERVED_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Read through ``client``; the other arguments bound the wait and the polling."""
        self._tasks: PaperlessTaskReader = PaperlessTaskReader(client)
        self._wait: float = wait_seconds
        self._poll: float = poll_seconds
        self._visible: float = visible_seconds
        self._unobserved: float = unobserved_seconds
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._clock: Callable[[], float] = clock

    async def confirmed(
        self, answer: Queued, failed: Callable[[str], Validation]
    ) -> Unconfirmed | None:
        """None when the change is made, ``Unconfirmed`` when that cannot be told.

        Raises:
            Validation: ``failed(task_id)``, when the task failed or was revoked.
        """
        outcome = await self._settled(answer.task_id)
        if isinstance(outcome, Unconfirmed):
            return outcome
        if outcome in {"failure", "revoked"}:
            raise failed(answer.task_id)
        return None

    async def _settled(self, task_id: str) -> TaskState | Unconfirmed:
        """The final state of the task, or why it could not be followed to its end.

        Only a 403 and a row that never shows up end the wait early (after the grace); other
        errors, also after the row was seen, are waited out until the deadline.
        """
        started = self._clock()
        while True:
            row = None
            unreadable: PaperlessMcpError | None = None
            try:
                row = await self._tasks.get_task(task_id)
            except PermissionDenied:
                return await self._unobserved_for(task_id, FORBIDDEN_REASON)
            except NotFound:
                row = None
            except PaperlessMcpError as error:
                unreadable = error
            elapsed = self._clock() - started
            if row is not None and row.is_final:
                return row.state
            if row is None and unreadable is None and elapsed >= self._visible:
                return await self._unobserved_for(task_id, INVISIBLE_REASON)
            if elapsed >= self._wait:
                reason = (
                    UNREADABLE_REASON
                    if unreadable is not None
                    else f"it was still running after {self._wait:.0f} seconds"
                )
                return Unconfirmed(task_id=task_id, reason=reason)
            await self._sleep(self._poll)

    async def _unobserved_for(self, task_id: str, reason: str) -> Unconfirmed:
        """Give a task that cannot be followed its grace to run, then report it."""
        await self._sleep(self._unobserved)
        return Unconfirmed(task_id=task_id, reason=reason)
