"""The tasks toolset: the list of background tasks, marking them as seen, starting some by hand.

Opt-in (``PAPERLESS_MCP_TOOLSETS=core,tasks``) and apart from user administration (decision-93).
Following one task by its task_id is ``get_task`` of the core toolset. The tasks that retrain or
re-index need ``confirm`` (decision-26).
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import AwareDatetime, Field

from mcp_server_paperless.domain.documents import NameOrId
from mcp_server_paperless.domain.task_lists import (
    Acknowledged,
    RunnableTask,
    StartedTask,
    TaskList,
    TaskOrdering,
    TaskType,
    TriggerSource,
)
from mcp_server_paperless.domain.tasks import TaskState
from mcp_server_paperless.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "tasks"
PAGE_SIZE = 25
LIST_LIMIT = 100

provider = LocalProvider()

type PositiveId = Annotated[int, Field(ge=1)]
type Page = Annotated[int, Field(ge=1, description="Page number, from 1.")]
type Size = Annotated[int, Field(ge=1, le=LIST_LIMIT, description="Rows per page, at most 100.")]


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_tasks"])
@boundary
async def list_tasks(  # noqa: PLR0913 -- one filter each, described for the LLM
    ctx: Context,
    *,
    state: Annotated[
        TaskState | list[TaskState] | None,
        Field(description="Only tasks in this state, or in one of these, e.g. failure."),
    ] = None,
    task_type: Annotated[
        TaskType | list[TaskType] | None,
        Field(description="Only this kind, or one of these, e.g. consume_file (an upload)."),
    ] = None,
    trigger_source: Annotated[
        TriggerSource | list[TriggerSource] | None,
        Field(description="Only what this, or one of these, started."),
    ] = None,
    acknowledged: Annotated[
        bool | None, Field(description="false: only tasks nobody acknowledged yet.")
    ] = None,
    is_complete: Annotated[
        bool | None, Field(description="true: only ended tasks; false: only waiting or running.")
    ] = None,
    owner: Annotated[NameOrId | None, Field(description="Username or id of the owner.")] = None,
    name: Annotated[
        str | None,
        Field(
            description=(
                "Part of the uploaded file's name, or of the words for type or source in the "
                "user's language."
            )
        ),
    ] = None,
    result: Annotated[
        str | None,
        Field(description="Part of a reason or error; a number also finds a document id."),
    ] = None,
    created_from: Annotated[
        AwareDatetime | None, Field(description="ISO time with zone, included.")
    ] = None,
    created_to: Annotated[
        AwareDatetime | None, Field(description="ISO time with zone, included.")
    ] = None,
    ordering: Annotated[
        TaskOrdering, Field(description="Sort field, - for descending; equal ones newest first.")
    ] = "-created",
    page: Page = 1,
    page_size: Size = PAGE_SIZE,
) -> TaskList:
    """List background tasks, newest first: uploads, mail fetches, sanity checks, training.

    Each row has the database id acknowledge_tasks takes, the task_id get_task takes, its state
    and, for a failure, the first line of the error; get_task shows all of it. Filters combine;
    several values of one filter mean any of them. count is the total; pass next_page as page for
    more. Staff users see every task, others their own and those without an owner. Needs the
    view_paperlesstask permission.
    """
    return await services(ctx).task_lists.list_tasks(
        state=state,
        task_type=task_type,
        trigger_source=trigger_source,
        acknowledged=acknowledged,
        is_complete=is_complete,
        owner=owner,
        name=name,
        result=result,
        created_from=created_from,
        created_to=created_to,
        ordering=ordering,
        page=page,
        page_size=page_size,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["acknowledge_tasks"])
@boundary
async def acknowledge_tasks(
    ctx: Context,
    task_ids: Annotated[
        list[PositiveId],
        Field(
            min_length=1,
            max_length=LIST_LIMIT,
            description="Database ids from list_tasks (the id field, not task_id); each once.",
        ),
    ],
) -> Acknowledged:
    """Mark tasks as seen, e.g. failed ones after you looked at them.

    They no longer show as new in the web UI; nothing is deleted and nothing needs confirm. Needs
    the change_paperlesstask permission.
    """
    return await services(ctx).task_lists.acknowledge_tasks(task_ids)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["run_task"])
@boundary
async def run_task(
    ctx: Context,
    task_type: Annotated[
        RunnableTask,
        Field(
            description=(
                "sanity_check: check the archive for problems, changes nothing. "
                "train_classifier: retrain automatic matching. llm_index: rebuild the AI index."
            )
        ),
    ],
    *,
    confirm: Confirm = False,
) -> StartedTask:
    """Start a background task by hand and return its task_id; follow it with get_task.

    A sanity check only reads and needs no confirm. Training the classifier replaces the model
    that tags new documents automatically, and rebuilding the AI index sends the text of every
    document to the configured AI service at its cost, so both need confirm: without it a dialog,
    or an error for you to show the user, first says what happens (see confirm). Superusers only.
    """
    return await services(ctx).task_lists.run_task(task_type, confirm=confirm)
