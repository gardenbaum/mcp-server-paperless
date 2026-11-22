"""TaskWaiter: the wait for the task Paperless 3.5 queues behind a document operation (PLMCP-46)."""

from typing import TYPE_CHECKING

import httpx2
import pytest

from paperless_mcp.domain.errors import UpstreamTimeout
from paperless_mcp.paperless.queued import Queued, TaskWaiter

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
    """The task list: one answer per read (the last repeats), or a failing status."""

    def __init__(self, *answers: list[dict[str, object]], status: int = 200) -> None:
        self.answers: list[list[dict[str, object]]] = list(answers)
        self.status: int = status
        self.reads: list[dict[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.reads.append(dict(request.url.params))
        if self.status != 200:
            return httpx2.Response(self.status, json={"detail": "no"})
        rows = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        body = {"count": len(rows), "next": None, "previous": None, "results": rows}
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
        make_client(tasks),
        wait_seconds=5.0,
        poll_seconds=0.5,
        visible_seconds=1.0,
        unobserved_seconds=2.0,
        sleep=clock.sleep,
        clock=clock.time,
    )


async def test_an_answer_without_a_task_id_is_done_and_reads_nothing(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks([]), Clock()

    state = await waiter(make_client, tasks, clock).settled(Queued(result="OK"))

    assert (state, tasks.reads, clock.sleeps) == (None, [], [])


@pytest.mark.parametrize("final", ["success", "failure", "revoked"])
async def test_the_task_is_followed_until_it_has_ended(
    make_client: ClientFactory, final: str
) -> None:
    tasks, clock = Tasks([row("pending")], [row("started")], [row(final)]), Clock()

    state = await waiter(make_client, tasks, clock).settled(Queued(result="OK", task_id=TASK))

    assert state == final
    assert tasks.reads == [{"task_id": TASK}] * 3
    assert clock.sleeps == [0.5, 0.5]


async def test_other_tasks_in_the_answer_are_not_taken_for_ours(
    make_client: ClientFactory,
) -> None:
    other = row("failure", "5b8f0c2e-0000-4000-8000-000000000002")
    tasks, clock = Tasks([other], [other, row("success")]), Clock()

    state = await waiter(make_client, tasks, clock).settled(Queued(result="OK", task_id=TASK))

    assert state == "success"


async def test_a_task_that_stays_invisible_is_unknown_after_the_grace(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks([]), Clock()

    state = await waiter(make_client, tasks, clock).settled(Queued(result="OK", task_id=TASK))

    assert state is None
    # The grace the task is given in place of being followed comes after the invisible second.
    assert clock.now == 1.0 + 2.0


@pytest.mark.parametrize("status", [403, 500])
async def test_a_task_list_that_cannot_be_read_leaves_the_outcome_unknown(
    make_client: ClientFactory, status: int
) -> None:
    tasks, clock = Tasks([], status=status), Clock()

    state = await waiter(make_client, tasks, clock).settled(Queued(result="OK", task_id=TASK))

    assert (state, clock.sleeps) == (None, [2.0])


async def test_a_task_still_running_after_the_wait_is_a_timeout_that_names_it(
    make_client: ClientFactory,
) -> None:
    tasks, clock = Tasks([row("started")]), Clock()

    with pytest.raises(UpstreamTimeout, match=TASK) as raised:
        _ = await waiter(make_client, tasks, clock).settled(Queued(result="OK", task_id=TASK))

    assert "get_task" in (raised.value.hint or "")
    assert clock.now == 5.0
