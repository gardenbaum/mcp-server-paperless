"""The workflows toolset: workflows that Paperless runs by itself, with triggers and actions.

A workflow has triggers (events with filters) and actions (what to do with a matching document).
Taxonomy is given by name or id; types by number or name. Anything that acts beyond filing,
meaning trash, e-mail, webhook, removal or a change of owner or permissions, needs ``confirm``
while the workflow is enabled (decision-21). The toolset is opt-in.
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from mcp_server_paperless.domain.documents import NameOrId
from mcp_server_paperless.domain.workflows import (
    ActionInput,
    TriggerInput,
    WorkflowCreate,
    WorkflowDeletion,
    WorkflowEdit,
    WorkflowInfo,
    WorkflowListing,
)
from mcp_server_paperless.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "workflows"

provider = LocalProvider()

type Workflow = Annotated[NameOrId, Field(description="The workflow: its name or id.")]
type Triggers = Annotated[
    list[TriggerInput] | None,
    Field(description="The triggers that start the workflow; see the tool description."),
]
type Actions = Annotated[
    list[ActionInput] | None,
    Field(description="The actions to apply, in order; see the tool description."),
]
type Enabled = Annotated[
    bool | None, Field(description="Whether the workflow runs. Switching it on may need confirm.")
]
type Order = Annotated[int | None, Field(description="Workflows run in this order, lowest first.")]

REFERENCE = """\
Triggers: 'consumption' (before a document is stored; needs a path, filename or mailrule filter),
'added' (after it is stored), 'updated', 'scheduled' (counts from a date of the document).
added, updated and scheduled can filter by content (match with matching_algorithm), tags,
correspondents, document types, storage paths and a custom field query. Taxonomy values are names
or ids, as in search_documents.

Actions run in order on the matching document: 'assignment' (assign_title as a template such as
'{{ correspondent }} {{ created_year }}', tags, correspondent, document type, storage path, owner,
view and change permissions, custom fields with values), 'removal' (remove_all_* flags and
remove_* lists), 'email' (email: subject, body, to, include_document), 'webhook' (a POST request:
webhook: url, headers, params or body, include_document), 'move to trash' (undo with
restore_from_trash, trash toolset, or in the web UI) and 'remote OCR' (consumption triggers only).
Settings of another action type than the one given are an error. Password removal and applying AI
suggestions cannot be written here; use the Paperless web UI. A 'manual' trigger (run by hand) is
listed but cannot be written here either.

A workflow runs by itself on every matching document once it is enabled (one with only manual
triggers runs when a user runs it by hand). Without confirm, an
enabled workflow that moves to trash, e-mails, calls a webhook, removes things or changes owner or
permissions needs the user's yes: a dialog, or an error for you to show the user, first says what
it would do (see confirm). Disabled workflows and plain filing (tags, title, correspondent and so
on) need no confirm. Needs the workflow permissions in Paperless.
"""

CREATE_DESCRIPTION = (
    """Create a workflow with its triggers and actions.

Returns it as stored. enabled defaults to true; pass enabled=false to build it first and switch it
on later with update_workflow.

"""
    + REFERENCE
)
UPDATE_DESCRIPTION = (
    """Change a workflow; only the settings you name change.

triggers and actions each replace the whole list (pass all that should remain; an empty list
removes them). To keep a webhook header value, which is never shown, send it as '(hidden)' on an
action that carries the id it has in get_workflow (only while the URL keeps its scheme, host
and port); headers left out of a webhook are removed. Returns the workflow as stored.

"""
    + REFERENCE
)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_workflows"])
@boundary
async def list_workflows(ctx: Context) -> WorkflowListing:
    """List the workflows of Paperless with their triggers and actions.

    Settings show names for tags, correspondents and so on, and only what is not at its default;
    they use the names create_workflow takes. Webhook header values and passwords are never shown.
    Needs the view permission for workflows in Paperless.
    """
    return await services(ctx).workflows.list()


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_workflow"])
@boundary
async def get_workflow(ctx: Context, workflow: Workflow) -> WorkflowInfo:
    """Read one workflow with its triggers and actions.

    The workflow matches by exact name, then ignoring case and accents, or by id; an unknown or
    ambiguous name is an error that lists the closest names or candidates.
    """
    return await services(ctx).workflows.get(workflow)


@provider.tool(
    tags={TOOLSET}, annotations=ANNOTATIONS["create_workflow"], description=CREATE_DESCRIPTION
)
@boundary
async def create_workflow(  # noqa: PLR0913 -- one parameter per setting of a workflow
    ctx: Context,
    *,
    name: Annotated[str, Field(min_length=1, max_length=256, description="A name not yet taken.")],
    triggers: Triggers = None,
    actions: Actions = None,
    enabled: Annotated[bool, Field(description="Whether the workflow runs once created.")] = True,
    order: Order = None,
    confirm: Confirm = False,
) -> WorkflowInfo:
    """Create a workflow (the description is CREATE_DESCRIPTION)."""
    spec = WorkflowCreate(
        name=name,
        triggers=tuple(triggers or ()),
        actions=tuple(actions or ()),
        enabled=enabled,
        order=order,
    )
    return await services(ctx).workflows.create(spec, confirm=confirm)


@provider.tool(
    tags={TOOLSET}, annotations=ANNOTATIONS["update_workflow"], description=UPDATE_DESCRIPTION
)
@boundary
async def update_workflow(  # noqa: PLR0913 -- one parameter per setting of a workflow
    ctx: Context,
    workflow: Workflow,
    *,
    name: Annotated[str | None, Field(description="A new name, not yet taken.")] = None,
    triggers: Triggers = None,
    actions: Actions = None,
    enabled: Enabled = None,
    order: Order = None,
    confirm: Confirm = False,
) -> WorkflowInfo:
    """Change a workflow (the description is UPDATE_DESCRIPTION)."""
    edit = WorkflowEdit(
        name=name,
        triggers=None if triggers is None else tuple(triggers),
        actions=None if actions is None else tuple(actions),
        enabled=enabled,
        order=order,
    )
    return await services(ctx).workflows.update(workflow, edit, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_workflow"])
@boundary
async def delete_workflow(
    ctx: Context, workflow: Workflow, *, confirm: Confirm = False
) -> WorkflowDeletion:
    """Delete a workflow for good: it has no trash. No document is touched.

    The workflow stops running at once. Without confirm a dialog, or an error for you to show the
    user, first says how many triggers and actions are lost (see confirm).
    """
    return await services(ctx).workflows.delete(workflow, confirm=confirm)
