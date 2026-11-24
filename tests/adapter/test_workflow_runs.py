"""PaperlessWorkflowWriter.run_workflow: the request and what its queued task tells (PLMCP-80.4)."""

import json
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from adapter.test_queued import TASK, Clock
from adapter.test_queued import row as task_row
from mcp_server_paperless.domain.errors import (
    PaperlessMcpError,
    PermissionDenied,
    TaskFailed,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.workflows import WorkflowRun
from mcp_server_paperless.paperless.client import RetryPolicy
from mcp_server_paperless.paperless.workflows import PaperlessWorkflowWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-75")]

type Json = dict[str, object]


class Paperless:
    """Queues the run and answers its task with `result`; `tasks` replaces the task list."""

    def __init__(
        self, status: str = "success", message: object = None, *, tasks: int | None = None
    ) -> None:
        self.row: Json = {**task_row(status), "result_data": {"error_message": message}}
        self.tasks: int | None = tasks
        self.answer: httpx2.Response = httpx2.Response(200, json={"result": "OK", "task_id": TASK})
        self.sent: list[tuple[str, str, object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/tasks/":
            if self.tasks is not None:
                return httpx2.Response(self.tasks, json={"detail": "no"})
            body = {"count": 1, "next": None, "previous": None, "results": [self.row]}
            return httpx2.Response(200, json=body)
        payload = cast("object", json.loads(request.content))
        self.sent.append((request.method, request.url.path, payload))
        return self.answer


def writer(make_client: ClientFactory, paperless: Paperless) -> PaperlessWorkflowWriter:
    client = make_client(paperless, retry=RetryPolicy(attempts=1))
    return PaperlessWorkflowWriter(client, sleep=Clock().sleep)


@pytest.mark.parametrize("filters", [True, False])
async def test_a_run_is_one_bulk_edit_request(make_client: ClientFactory, *, filters: bool) -> None:
    paperless = Paperless()

    run = await writer(make_client, paperless).run_workflow(3, (4, 7), filter_documents=filters)

    expected = {
        "documents": [4, 7],
        "method": "run_workflow",
        "parameters": {"workflow": 3, "filter_documents": filters},
    }
    assert paperless.sent == [("POST", "/api/documents/bulk_edit/", expected)]
    assert run == WorkflowRun(task_id=TASK)


async def test_the_documents_a_failed_task_names_are_told(make_client: ClientFactory) -> None:
    text = "Workflow 3 failed for document(s): [7, 4]. Check logs for details."

    run = await writer(make_client, Paperless("failure", text)).run_workflow(
        3, (4, 5, 7), filter_documents=True
    )

    assert run == WorkflowRun(task_id=TASK, failed=(7, 4))


@pytest.mark.parametrize(
    ("status", "text"),
    [
        ("failure", "Workflow 33 failed for document(s): [7]. Check logs for details."),
        ("failure", "Workflow 3 failed for document(s): [8]. Check logs for details."),
        ("failure", "Workflow 3 failed for document(s): [7, 8]. Check logs for details."),
        ("failure", "Workflow 3 failed for document(s): []. Check logs for details."),
        ("failure", "Workflow 3 failed for document(s): [7, x]. Check logs for details."),
        ("failure", "x Workflow 3 failed for document(s): [7]. Check logs for details."),
        ("failure", "Workflow 3 failed for document(s): [7]. Check logs for details. And more."),
        ("failure", "Insufficient permissions to execute bulk operation"),
        ("failure", None),
        ("revoked", "Workflow 3 failed for document(s): [7]. Check logs for details."),
    ],
)
async def test_a_failed_task_that_does_not_name_documents_of_the_run_tells_of_none(
    make_client: ClientFactory, status: str, text: str | None
) -> None:
    with pytest.raises(TaskFailed) as raised:
        _ = await writer(make_client, Paperless(status, text)).run_workflow(
            3, (4, 7), filter_documents=True
        )

    assert raised.value.message.startswith("The task of the workflow run failed")
    assert (repr(text)[1:60] in raised.value.message) is (text is not None)


async def test_what_paperless_says_of_a_failure_is_quoted_and_without_credentials(
    make_client: ClientFactory,
) -> None:
    text = "cannot reach https://user:hush@example.org/x\nNothing was changed."

    with pytest.raises(TaskFailed) as raised:
        _ = await writer(make_client, Paperless("failure", text)).run_workflow(
            3, (4,), filter_documents=True
        )

    assert "hush" not in raised.value.message
    assert "\n" not in raised.value.message


async def test_a_user_who_may_not_view_tasks_cannot_tell_how_it_ended(
    make_client: ClientFactory,
) -> None:
    run = await writer(make_client, Paperless(tasks=403)).run_workflow(
        3, (4,), filter_documents=True
    )

    assert run.unconfirmed is not None
    assert run.unconfirmed.task_id == TASK
    assert "may not view tasks" in run.unconfirmed.reason
    assert run.failed == ()


async def test_a_rate_limit_ends_the_wait_at_once(make_client: ClientFactory) -> None:
    run = await writer(make_client, Paperless(tasks=429)).run_workflow(
        3, (4,), filter_documents=True
    )

    assert run.unconfirmed is not None
    assert run.unconfirmed.rate_limited


async def test_a_refused_run_says_that_nothing_ran_and_what_is_needed(
    make_client: ClientFactory,
) -> None:
    paperless = Paperless()
    paperless.answer = httpx2.Response(403, text="Insufficient permissions")

    with pytest.raises(PermissionDenied) as raised:
        _ = await writer(make_client, paperless).run_workflow(3, (4,), filter_documents=True)

    assert raised.value.message == (
        "Paperless refused to run the workflow; it checks every document first, so nothing was run."
    )
    assert raised.value.hint == (
        "You need the permissions to view workflows and to change documents, and must own every "
        "document (or it must have no owner)."
    )


async def test_a_workflow_paperless_does_not_run_is_a_validation_error(
    make_client: ClientFactory,
) -> None:
    paperless = Paperless()
    paperless.answer = httpx2.Response(400, json={"non_field_errors": ["Workflow is not enabled"]})

    with pytest.raises(Validation, match="Workflow is not enabled"):
        _ = await writer(make_client, paperless).run_workflow(3, (4,), filter_documents=True)


async def test_a_plain_text_400_does_not_say_whether_the_run_was_queued(
    make_client: ClientFactory,
) -> None:
    paperless = Paperless()
    said = "Error performing bulk edit, check logs for more detail."
    paperless.answer = httpx2.Response(400, text=said, headers={"content-type": "text/html"})

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await writer(make_client, paperless).run_workflow(3, (4,), filter_documents=True)

    assert not isinstance(raised.value, Validation)
    assert raised.value.message == (
        "Paperless reported an error while it queued the run, without saying whether it runs."
    )


async def test_a_run_is_never_sent_twice(make_client: ClientFactory) -> None:
    paperless = Paperless()
    paperless.answer = httpx2.Response(503)
    client = make_client(paperless, retry=RetryPolicy(attempts=3))

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessWorkflowWriter(client).run_workflow(3, (4,), filter_documents=True)

    assert len(paperless.sent) == 1
