"""TaskWaiter: the wait for the task Paperless 3.5 queues behind a document operation (PLMCP-46)."""

from typing import TYPE_CHECKING

import httpx2
import pytest

from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.domain.tasks import Unconfirmed
from mcp_server_paperless.paperless.client import RetryPolicy
from mcp_server_paperless.paperless.queued import Queued, TaskWaiter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

TASK = "5b8f0c2e-0000-4000-8000-000000000001"


def row(status: str, task_id: str = TASK) -> dict[str, object]:
    """A task row as 3.5.1 lists it (only the fields the waiter reads matter)."""
    return {
        "id": 1,
        "task_id": task_id,
        "task_type": "bulk_update",
        "task_type_display": "Bulk Update",
        "trigger_source": "api_upload",
        "trigger_source_display": "API Upload",
        "status": status,
        "status_display": status,
        "date_created": "2026-11-22T12:20:54.711989Z",
        "date_started": None,
        "date_done": None,
        "duration_seconds": None,
        "wait_time_seconds": None,
        "input_data": {"operation": "modify_tags", "document_count": 1},
        "result_data": None,
        "related_document_ids": [],
        "acknowledged": False,
        "owner": 2,
    }


class Tasks:
    """The task list: one answer per read (the last repeats). An answer is rows or a status."""

    def __init__(self, *answers: list[dict[str, object]] | int) -> None:
        self.answers: list[list[dict[str, object]] | int] = list(answers)
        self.reads: list[dict[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.reads.append(dict(request.url.params))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, int):
            return httpx2.Response(answer, json={"detail": "no"})
        body = {"count": len(answer), "next": None, "previous": None, "results": answer}
        return httpx2.Response(200, json=body)


class Clock:
    """A clock the sleeps advance, so the waits need no real time."""

    def __init__(self) -> None:
        self.now: float = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def waiter(make_client: ClientFactory, tasks: Tasks, clock: Clock) -> TaskWaiter:
    return TaskWaiter(
        make_client(tasks, retry=RetryPolicy(attempts=1)),
        wait_seconds=5.0,
        poll_seconds=0.5,
        visible_seconds=1.0,
        unobserved_seconds=2.0,
        sleep=clock.sleep,
        clock=clock.time,
    )


ANSWER = Queued(result="OK", task_id=TASK)


async def test_an_answer_without_a_task_id_is_confirmed_and_reads_nothing(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks([]), Clock()

    outcome = await waiter(make_client, tasks, clock).confirmed(Queued(result="OK"), _never)

    assert (outcome, tasks.reads, clock.sleeps) == (None, [], [])


def _never(_: str) -> Validation:
    return Validation("failed")


@pytest.mark.parametrize("final", ["failure", "revoked"])
async def test_a_task_that_failed_or_was_revoked_raises_what_the_caller_made_of_it(
    make_client: ClientFactory, final: str
) -> None:
    tasks, clock = Tasks([row("pending")], [row(final)]), Clock()

    with pytest.raises(Validation, match=TASK):
        _ = await waiter(make_client, tasks, clock).confirmed(
            ANSWER, lambda task: Validation(f"task {task}")
        )


async def test_the_task_is_followed_until_it_has_succeeded(make_client: ClientFactory) -> None:
    tasks, clock = Tasks([row("pending")], [row("started")], [row("success")]), Clock()

    outcome = await waiter(make_client, tasks, clock).confirmed(ANSWER, _never)

    assert outcome is None
    assert tasks.reads == [{"task_id": TASK}] * 3
    assert clock.sleeps == [0.5, 0.5]


async def test_other_tasks_in_the_answer_are_not_taken_for_ours(
    make_client: ClientFactory,
) -> None:
    other = row("failure", "5b8f0c2e-0000-4000-8000-000000000002")
    tasks, clock = Tasks([other], [other, row("success")]), Clock()

    outcome = await waiter(make_client, tasks, clock).confirmed(ANSWER, _never)

    assert outcome is None


async def test_a_task_that_stays_invisible_is_unconfirmed_after_the_grace(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks([]), Clock()

    outcome = await waiter(make_client, tasks, clock).confirmed(ANSWER, _never)

    assert isinstance(outcome, Unconfirmed)
    assert outcome.task_id == TASK
    assert "did not show up" in outcome.reason
    # The grace the task is given in place of being followed comes after the invisible second.
    assert clock.now == 1.0 + 2.0


async def test_a_user_who_may_not_view_tasks_gets_an_unconfirmed_outcome_after_the_grace(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks(403), Clock()

    outcome = await waiter(make_client, tasks, clock).confirmed(ANSWER, _never)

    assert outcome == Unconfirmed(
        task_id=TASK, reason="the Paperless user may not view tasks, so it cannot be followed"
    )
    assert clock.sleeps == [2.0]


async def test_a_transient_error_after_the_task_was_seen_is_waited_out(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks([row("started")], 503, 500, [row("success")]), Clock()

    outcome = await waiter(make_client, tasks, clock).confirmed(ANSWER, _never)

    assert outcome is None
    assert clock.sleeps == [0.5, 0.5, 0.5]


async def test_a_task_list_that_stays_unreadable_is_unconfirmed_at_the_deadline(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks(503), Clock()

    outcome = await waiter(make_client, tasks, clock).confirmed(ANSWER, _never)

    assert isinstance(outcome, Unconfirmed)
    assert "could not be read" in outcome.reason
    assert clock.now == 5.0


async def test_a_task_still_running_after_the_wait_is_unconfirmed_and_named(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks([row("started")]), Clock()

    outcome = await waiter(make_client, tasks, clock).confirmed(ANSWER, _never)

    assert outcome == Unconfirmed(task_id=TASK, reason="it was still running after 5 seconds")
    assert clock.now == 5.0
