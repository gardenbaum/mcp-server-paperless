"""Waiting for the work Paperless 3.5 queues behind the document operations.

Paperless 3.4 changed tags, fields, references, deletions and merges of versions within the
request and answered ``{"result": "OK"}``. From 3.5 the document operations (``bulk_edit``,
``delete``, ``reprocess``, ``rotate``, ``merge``, ``merge_as_versions``, ``edit_pdf`` and
``remove_password``) only queue a ``bulk_update`` task and answer ``{"result": "OK", "task_id":
...}``: the change is made some milliseconds later, and a failure shows only in the task.

``TaskWaiter`` makes the adapter keep its 3.4 meaning, "done when the call returns": it follows
the task named in the answer until it ends. An answer without a task id (3.4) needs no wait. A
user who may not view tasks, a task that stays invisible, or a task list that cannot be read
leave the outcome unknown (``None``): the request was accepted, so no error is raised that would
invite a repeat. Then the waiter only gives the worker a short, fixed grace to make the change,
which is a best effort: a user who may not view tasks (``view_paperlesstask``) cannot be told
when the change is made.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel

from paperless_mcp.domain.errors import PaperlessMcpError, UpstreamTimeout, Validation
from paperless_mcp.domain.tasks import FINAL_STATES, TaskState
from paperless_mcp.paperless.pagination import Page

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from paperless_mcp.paperless.client import PaperlessClient

TASKS = "/api/tasks/"
# How long the task of a request may run, how often it is read meanwhile, and how long its row
# may be missing before it is taken to be invisible to this user.
WAIT_SECONDS: Final = 60.0
POLL_SECONDS: Final = 0.2
VISIBLE_SECONDS: Final = 3.0
# What a task that cannot be followed is given to run, in place of following it.
UNOBSERVED_SECONDS: Final = 2.0


class Queued(BaseModel):
    """The answer of a document operation: ``task_id`` is named from Paperless 3.5 on."""

    result: str
    task_id: str | None = None


FAILED_HINT: Final = (
    "Paperless may have applied part of it before it failed; read the documents again before "
    "repeating it, and check the Paperless logs for the reason."
)
TIMEOUT_HINT: Final = (
    "The change is queued and is still applied; read the task with get_task before repeating it."
)


def not_applied(what: str) -> Validation:
    """The error for a queued ``what`` whose task failed (3.4 answered with a 400)."""
    return Validation(f"Paperless could not apply the {what}: its task failed.", hint=FAILED_HINT)


def _timeout(task_id: str, wait: float) -> UpstreamTimeout:
    message = f"Paperless accepted the change but task {task_id} did not finish in {wait:.0f} s."
    return UpstreamTimeout(message, hint=TIMEOUT_HINT)


class _Row(BaseModel):
    task_id: str
    status: TaskState


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
        self._client: PaperlessClient = client
        self._wait: float = wait_seconds
        self._poll: float = poll_seconds
        self._visible: float = visible_seconds
        self._unobserved: float = unobserved_seconds
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._clock: Callable[[], float] = clock

    async def settled(self, answer: Queued) -> TaskState | None:
        """The final state of the task ``answer`` names, or None if it is not known.

        None: the answer names no task (Paperless 3.4 did the work in the request), or the task
        cannot be followed (not visible to the user, or the list cannot be read); the latter
        waits the fixed grace first.

        Raises:
            UpstreamTimeout: The task was still running after the wait; the work stays queued.
        """
        if answer.task_id is None:
            return None
        started = self._clock()
        while True:
            try:
                page = await self._client.get(TASKS, Page[_Row], params={"task_id": answer.task_id})
            except PaperlessMcpError:
                await self._sleep(self._unobserved)
                return None
            row = next((r for r in page.results if r.task_id == answer.task_id), None)
            elapsed = self._clock() - started
            if row is not None and row.status in FINAL_STATES:
                return row.status
            if row is None and elapsed >= self._visible:
                await self._sleep(self._unobserved)
                return None
            if elapsed >= self._wait:
                raise _timeout(answer.task_id, self._wait)
            await self._sleep(self._poll)
