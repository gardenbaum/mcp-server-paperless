"""WorkflowReader and WorkflowWriter on ``/api/workflows/``.

A workflow carries its triggers and actions as nested objects. Paperless 3.4.2 stores each of them
as an object of its own and replaces the nested lists of a workflow as a whole, so a write sends
them without ids; the settings travel under Paperless's names, the type apart from them.
"""

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import BaseModel, ConfigDict, JsonValue

from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    TaskFailed,
    quoted,
)
from mcp_server_paperless.domain.tasks import Unconfirmed
from mcp_server_paperless.domain.workflows import (
    Workflow,
    WorkflowAction,
    WorkflowRun,
    WorkflowTrigger,
)
from mcp_server_paperless.paperless.pagination import MAX_ITEMS, Page, collect
from mcp_server_paperless.paperless.queued import FAILED_HINT, Queued, TaskWaiter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from mcp_server_paperless.domain.tasks import TaskStatus
    from mcp_server_paperless.domain.workflows import WorkflowValues
    from mcp_server_paperless.paperless.client import PaperlessClient

PATH = "/api/workflows/"
RUN: Final = "/api/documents/bulk_edit/"
# What the task of a run says when the workflow failed on some documents.
FAILED_ON: Final = re.compile(
    r"Workflow (\d+) failed for document\(s\): \[(\d+(?:, \d+)*)\]\. Check logs for details\."
)
# The nested objects of an action have ids of their own that mean nothing to a caller.
NESTED = ("email", "webhook")


class _Part(BaseModel):
    """A trigger or action: its id and type, and every other setting as it came."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: int | None = None
    type: int | None = None

    def kind(self) -> int:
        """The type; Paperless always sends one, so its absence is an error, not a guess."""
        if self.type is None:
            message = "Paperless sent a workflow trigger or action without a type."
            raise PaperlessMcpError(message)
        return self.type

    def settings(self) -> dict[str, JsonValue]:
        """What the part holds besides its id and type; nested objects lose their id."""
        settings: dict[str, JsonValue] = dict(self.model_extra or {})
        for name in NESTED:
            nested = settings.get(name)
            if isinstance(nested, dict):
                settings[name] = {k: v for k, v in nested.items() if k != "id"}
        return settings


class _Row(BaseModel):
    id: int
    name: str
    order: int = 0
    enabled: bool = True
    triggers: list[_Part]
    actions: list[_Part]


def _workflow(row: _Row) -> Workflow:
    return Workflow(
        id=row.id,
        name=row.name,
        order=row.order,
        enabled=row.enabled,
        triggers=tuple(
            WorkflowTrigger(id=t.id, type=t.kind(), settings=t.settings()) for t in row.triggers
        ),
        actions=tuple(
            WorkflowAction(id=a.id, type=a.kind(), settings=a.settings()) for a in row.actions
        ),
    )


def unseen_workflow(workflow_id: int) -> NotFound:
    """The error for a workflow that does not exist."""
    message = f"No workflow {workflow_id} exists."
    return NotFound(message, hint="Call list_workflows to see the workflows you can use.")


def _body(values: WorkflowValues) -> dict[str, object]:
    """The request body for the settings ``values`` sets, under Paperless's names."""
    body: dict[str, object] = {}
    if values.name is not None:
        body["name"] = values.name
    if values.order is not None:
        body["order"] = values.order
    if values.enabled is not None:
        body["enabled"] = values.enabled
    if values.triggers is not None:
        body["triggers"] = [{"type": t.type, **t.settings} for t in values.triggers]
    if values.actions is not None:
        body["actions"] = [{"type": a.type, **a.settings} for a in values.actions]
    return body


def run_refused() -> PermissionDenied:
    """The error for a 403 of a run."""
    message = (
        "Paperless refused to run the workflow; it checks every document first, so nothing was run."
    )
    hint = (
        "You need the permissions to view workflows and to change documents, and must own every "
        "document (or it must have no owner)."
    )
    return PermissionDenied(message, hint=hint)


def _failed_on(task: TaskStatus, workflow_id: int, document_ids: Sequence[int]) -> tuple[int, ...]:
    """The documents of a run its failed task names.

    Raises:
        TaskFailed: It names none, or others than those of the run.
    """
    said = FAILED_ON.fullmatch(task.message or "") if task.state == "failure" else None
    if said is not None and int(said[1]) == workflow_id:
        failed = tuple(int(number) for number in said[2].split(", "))
        if set(failed) <= set(document_ids):
            return failed
    why = f": {quoted(task.message, 200)}" if task.message else ""
    message = f"The task of the workflow run failed{why}."
    raise TaskFailed(message, hint=FAILED_HINT)


class PaperlessWorkflowReader:
    """``WorkflowReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def list_workflows(self) -> tuple[Workflow, ...]:
        """See ``WorkflowReader.list_workflows``.

        Raises:
            PaperlessMcpError: More than ``MAX_ITEMS`` workflows; resolving names on a part of
                them could pick the wrong one.
        """
        collected = await collect(self._client, PATH, Page[_Row])
        if collected.truncated:
            message = (
                f"Paperless has more than {MAX_ITEMS} workflows; this server reads up to that."
            )
            raise PaperlessMcpError(message)
        return tuple(_workflow(row) for row in collected.items)

    async def get_workflow(self, workflow_id: int) -> Workflow:
        """See ``WorkflowReader.get_workflow``."""
        try:
            return _workflow(await self._client.get(f"{PATH}{workflow_id}/", _Row))
        except NotFound:
            raise unseen_workflow(workflow_id) from None


class PaperlessWorkflowWriter:
    """``WorkflowWriter`` over the Paperless REST API."""

    def __init__(
        self,
        client: PaperlessClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Write through ``client``; ``sleep`` paces the wait for a queued run."""
        self._client: PaperlessClient = client
        self._waiter: TaskWaiter = TaskWaiter(client, sleep=sleep)

    async def create_workflow(self, name: str, values: WorkflowValues) -> Workflow:
        """See ``WorkflowWriter.create_workflow``: Paperless requires both lists, even empty."""
        body: dict[str, object] = {"triggers": [], "actions": [], **_body(values), "name": name}
        return _workflow(await self._client.call("POST", PATH, _Row, json=body))

    async def update_workflow(self, workflow_id: int, values: WorkflowValues) -> Workflow:
        """See ``WorkflowWriter.update_workflow``."""
        try:
            row = await self._client.call(
                "PATCH", f"{PATH}{workflow_id}/", _Row, json=_body(values)
            )
        except NotFound:
            raise unseen_workflow(workflow_id) from None
        return _workflow(row)

    async def delete_workflow(self, workflow_id: int) -> None:
        """See ``WorkflowWriter.delete_workflow``."""
        try:
            _ = await self._client.request("DELETE", f"{PATH}{workflow_id}/")
        except NotFound:
            raise unseen_workflow(workflow_id) from None

    async def run_workflow(
        self, workflow_id: int, document_ids: Sequence[int], *, filter_documents: bool
    ) -> WorkflowRun:
        """See ``WorkflowWriter.run_workflow``: a ``bulk_edit`` whose queued task is followed."""
        body: dict[str, JsonValue] = {
            "documents": list(document_ids),
            "method": "run_workflow",
            "parameters": {"workflow": workflow_id, "filter_documents": filter_documents},
        }
        try:
            queued = await self._client.call("POST", RUN, Queued, json=body)
        except PermissionDenied:
            raise run_refused() from None
        task = await self._waiter.ended(queued.task_id, stop_on_limit=True)
        if isinstance(task, Unconfirmed):
            return WorkflowRun(task_id=queued.task_id, unconfirmed=task)
        failed = () if task.state == "success" else _failed_on(task, workflow_id, document_ids)
        return WorkflowRun(task_id=queued.task_id, failed=failed)
