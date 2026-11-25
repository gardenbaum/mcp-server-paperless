"""The tasks toolset: background tasks and how Paperless is doing.

Opt-in (``PAPERLESS_MCP_TOOLSETS=core,tasks``) and apart from user administration (decision-93).
Following one task by its task_id is ``get_task`` of the core toolset. The tasks that retrain or
re-index need ``confirm`` (decision-26).
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import AwareDatetime, Field

from mcp_server_paperless.domain.documents import NameOrId
from mcp_server_paperless.domain.instance import SystemReport
from mcp_server_paperless.domain.task_lists import (
    Acknowledged,
    RunnableTask,
    StartedTask,
    TaskList,
    TaskOrdering,
    TaskOverview,
    TaskType,
    TriggerSource,
)
from mcp_server_paperless.domain.tasks import TaskState
from mcp_server_paperless.services.task_lists import MAX_DAYS
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


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_task_overview"])
@boundary
async def get_task_overview(  # noqa: PLR0913 -- one filter each, described for the LLM
    ctx: Context,
    *,
    days: Annotated[int, Field(ge=1, le=MAX_DAYS, description="What by_type covers.")] = 30,
    acknowledged: Annotated[
        bool | None, Field(description="false: count only tasks nobody acknowledged yet.")
    ] = None,
    task_type: Annotated[
        TaskType | list[TaskType] | None, Field(description="Count only this kind or these.")
    ] = None,
    trigger_source: Annotated[
        TriggerSource | list[TriggerSource] | None,
        Field(description="Count only what this, or one of these, started."),
    ] = None,
    owner: Annotated[NameOrId | None, Field(description="Username or id of the owner.")] = None,
    created_from: Annotated[
        AwareDatetime | None, Field(description="ISO time with zone, included.")
    ] = None,
    created_to: Annotated[
        AwareDatetime | None, Field(description="ISO time with zone, included.")
    ] = None,
    name: Annotated[str | None, Field(description="As in list_tasks.")] = None,
    result: Annotated[str | None, Field(description="As in list_tasks.")] = None,
) -> TaskOverview:
    """How the background tasks stand: how many need attention, what runs, each type's record.

    counts follows the filters; active and by_type do not (Paperless offers no filter there).
    What the user may not see is null and named in unavailable. Needs the view_paperlesstask
    permission; by_type needs that or view_system_monitoring. List tasks with list_tasks.
    """
    return await services(ctx).task_lists.overview(
        days=days,
        acknowledged=acknowledged,
        task_type=task_type,
        trigger_source=trigger_source,
        owner=owner,
        created_from=created_from,
        created_to=created_to,
        name=name,
        result=result,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_system_report"])
@boundary
async def get_system_report(
    ctx: Context,
    *,
    check_for_update: Annotated[
        bool,
        Field(
            description=(
                "Also let Paperless ask GitHub for its newest release, for at most 10 seconds; "
                "update is null inside where that failed."
            )
        ),
    ] = False,
) -> SystemReport:
    """Read all of Paperless's statistics and system status, to find out why something is wrong.

    More than get_instance_overview: file types, characters, highest serial number, inbox tags;
    host, database and its migrations, Redis and worker addresses, when the search index, the
    classifier, the sanity check and the AI index last ran, and task totals. URLs come without user
    name and password. status is null without the view_system_monitoring permission (staff and
    superusers have it). Takes a few seconds while no worker answers.
    """
    return await services(ctx).reports.report(check_for_update=check_for_update)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["acknowledge_tasks"])
@boundary
async def acknowledge_tasks(  # noqa: PLR0913 -- what to mark, and how it is confirmed
    ctx: Context,
    task_ids: Annotated[
        list[PositiveId] | None,
        Field(
            min_length=1,
            max_length=LIST_LIMIT,
            description="Database ids from list_tasks (the id field, not task_id); each once.",
        ),
    ] = None,
    *,
    all_unacknowledged: Annotated[
        bool, Field(description="Instead of ids: every task nobody acknowledged yet.")
    ] = False,
    dry_run: Annotated[
        bool, Field(description="Only say what would be marked; change nothing.")
    ] = False,
    confirm: Confirm = False,
    expected: Annotated[
        str | None,
        Field(description="With all_unacknowledged and confirm: the preview's expected value."),
    ] = None,
) -> Acknowledged:
    """Mark tasks as seen, e.g. failed ones after you looked at them.

    They no longer show as new in the web UI; nothing is deleted, and there is no way back. Ids
    need no confirm. all_unacknowledged does: without it a dialog, or an error for you to show the
    user, first says how many, the newest failures and whose they are (see confirm); without a
    dialog confirm counts only with the expected value this names. Staff users mark every user's
    tasks, others their own and those without an owner, also tasks that come in meanwhile. Needs
    the change_paperlesstask permission.
    """
    return await services(ctx).task_lists.acknowledge_tasks(
        task_ids,
        all_unacknowledged=all_unacknowledged,
        dry_run=dry_run,
        confirm=confirm,
        expected=expected,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["run_task"])
@boundary
async def run_task(
    ctx: Context,
    task_type: Annotated[
        RunnableTask,
        Field(
            description=(
                "sanity_check: check the archive for problems, changes nothing. "
                "train_classifier: retrain automatic matching. llm_index: update the AI index."
            )
        ),
    ],
    *,
    confirm: Confirm = False,
) -> StartedTask:
    """Start a background task by hand and return its task_id; follow it with get_task.

    Paperless starts only these three by hand and one per call; call once per type. A sanity check
    only reads and needs no confirm. Training the classifier replaces the model that tags new
    documents automatically. Updating the AI index sends the text of every document that is new or
    changed since the last time (the first time, or with another embedding model, of every
    document) to the configured service at its cost; with the index off it does nothing. Both need
    confirm: without it a dialog, or an error for you to show the user, first says what happens
    (see confirm). Superusers only.
    """
    return await services(ctx).task_lists.run_task(task_type, confirm=confirm)
