"""The workflows toolset through the assembled server against the seeded Paperless 3.4.2 stack.

Every workflow here is disabled or has a trigger that no file matches (`NEVER`): a live workflow
acts on documents other tests consume.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import drop_orphaned_workflow_parts
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import results
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]
type Connected = Client[FastMCPTransport]

# Workflows this module creates; the contract suite's cleanup removes leftovers of a crashed run.
SCRATCH = "contract-workflow-e2e-"
NEVER = {"type": "consumption", "filename": "contract-workflow-never-*.none"}


@pytest.fixture
async def client(stack: Stack) -> AsyncIterator[Connected]:
    server = create_server(
        paperless_settings(stack.base_url, stack.admin_token),
        server_settings(toolsets=["core", "workflows"]),
    )
    async with Client(server) as connected:
        yield connected
    # Whatever a test left behind, also when it failed.
    for row in results(stack.admin.get("/api/workflows/", params={"page_size": 100})):
        if str(row["name"]).startswith(SCRATCH):
            _ = stack.admin.delete(f"/api/workflows/{row['id']}/")
    drop_orphaned_workflow_parts(stack)


async def call(client: Connected, tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(client: Connected, tool: str, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def stored(stack: Stack, name: str) -> list[Json]:
    """The workflows of that name as Paperless itself lists them."""
    found = results(stack.admin.get("/api/workflows/", params={"name__iexact": name}))
    return [row for row in found if row["name"] == name]


async def test_a_workflow_is_filed_by_names_read_changed_and_deleted(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    created = await call(
        client,
        "create_workflow",
        name=name,
        enabled=False,
        triggers=[
            NEVER,
            {
                "type": "added",
                "has_tags": ["e2e-finance"],
                "has_any_correspondents": ["e2e-acme"],
                "has_not_document_types": ["e2e-invoice"],
            },
        ],
        actions=[
            {
                "type": "assignment",
                "assign_title": "{{ correspondent }} {{ created_year }}",
                "assign_tags": ["e2e-inbox"],
                "assign_storage_path": "e2e-archive",
                "assign_custom_fields_values": {"e2e-string": "filed"},
            }
        ],
    )

    [raw] = stored(stack, name)
    tags = {str(t["name"]): t["id"] for t in results(stack.admin.get("/api/tags/"))}
    [consumed, added] = cast("list[Json]", raw["triggers"])
    [assigned] = cast("list[Json]", raw["actions"])
    assert added["filter_has_tags"] == [tags["e2e-finance"]]
    assert assigned["assign_tags"] == [tags["e2e-inbox"]]
    assert consumed["filter_filename"] == NEVER["filename"]
    [shown_trigger, shown_added] = cast("list[Json]", created["triggers"])
    assert shown_trigger["settings"] == {"filename": NEVER["filename"]}
    assert shown_added["settings"] == {
        "has_tags": ["e2e-finance"],
        "has_any_correspondents": ["e2e-acme"],
        "has_not_document_types": ["e2e-invoice"],
    }
    [shown_action] = cast("list[Json]", created["actions"])
    settings = cast("Json", shown_action["settings"])
    assert settings["assign_tags"] == ["e2e-inbox"]
    assert settings["assign_storage_path"] == "e2e-archive"
    assert settings["assign_custom_fields"] == ["e2e-string"]
    assert settings["assign_custom_fields_values"] == {"e2e-string": "filed"}

    listed = {
        str(w["name"])
        for w in cast("list[Json]", (await call(client, "list_workflows"))["workflows"])
    }
    assert name in listed
    assert await call(client, "get_workflow", workflow=name.upper()) == created

    renamed = await call(
        client, "update_workflow", workflow=name, name=f"{name}-b", triggers=[NEVER], order=9
    )
    assert (renamed["name"], renamed["order"]) == (f"{name}-b", 9)
    assert [t["type_name"] for t in cast("list[Json]", renamed["triggers"])] == [
        "consumption started"
    ]

    text = await refused(client, "delete_workflow", workflow=f"{name}-b")
    assert "Nothing was changed" in text
    assert "its 1 trigger and 1 action are lost" in text
    assert len(stored(stack, f"{name}-b")) == 1
    gone = await call(client, "delete_workflow", workflow=f"{name}-b", confirm=True)
    assert gone["name"] == f"{name}-b"
    assert stored(stack, f"{name}-b") == []


async def test_a_workflow_that_acts_is_refused_until_confirmed_and_secrets_stay_hidden(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    hook = {"url": "https://hooks.example.org/in?token=s3", "headers": {"X-Token": "real-token"}}
    acting: Json = {
        "name": name,
        "triggers": [NEVER],
        "actions": [
            {"type": "webhook", "webhook": hook},
            {"type": "email", "email": {"subject": "s", "body": "b", "to": "a@example.org"}},
        ],
    }

    text = await refused(client, "create_workflow", **acting)
    assert stored(stack, name) == []
    assert "send a POST request to hooks.example.org" in text
    assert "send an e-mail to a@example.org" in text
    assert "s3" not in text
    assert "real-token" not in text

    made = await call(client, "create_workflow", confirm=True, **acting)
    assert "real-token" not in str(made)
    [raw] = stored(stack, name)
    [webhook, _] = cast("list[Json]", raw["actions"])
    assert cast("Json", webhook["webhook"])["headers"] == {"X-Token": "real-token"}

    off = await call(client, "update_workflow", workflow=name, enabled=False)
    assert off["enabled"] is False
    assert "Confirming would change the workflow" in await refused(
        client, "update_workflow", workflow=name, enabled=True
    )
    [first, _] = cast("list[Json]", off["actions"])
    kept = await call(
        client,
        "update_workflow",
        workflow=name,
        actions=[
            {
                "type": "webhook",
                "id": first["id"],
                "webhook": {
                    "url": "https://hooks.example.org/other",
                    "headers": {"X-Token": "(hidden)"},
                },
            }
        ],
    )
    assert [a["type_name"] for a in cast("list[Json]", kept["actions"])] == ["webhook"]
    [raw] = stored(stack, name)
    [webhook] = cast("list[Json]", raw["actions"])
    assert cast("Json", webhook["webhook"])["headers"] == {"X-Token": "real-token"}
    assert cast("Json", webhook["webhook"])["url"] == "https://hooks.example.org/other"


async def test_an_unsupported_type_and_a_misplaced_setting_are_refused_with_an_error(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"

    passwords = await refused(
        client, "create_workflow", name=name, actions=[{"type": "password removal"}]
    )
    misplaced = await refused(
        client,
        "create_workflow",
        name=name,
        actions=[{"type": "move to trash", "assign_title": "x"}],
    )
    nothing = await refused(client, "create_workflow", name=name, triggers=[{"type": "hourly"}])

    assert "cannot be written by this server" in passwords
    assert "'assign_title' belongs to assignment actions" in misplaced
    assert "No workflow trigger type is called 'hourly'" in nothing
    assert stored(stack, name) == []
