"""The tasks toolset through the assembled server on the E2E Paperless.

The only task run is the sanity check, which changes nothing, and its row is acknowledged.
"""

import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume, num, obj, parse, results
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from e2e.helpers import Json, Stack

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-26"),
    pytest.mark.decision("decision-93"),
    pytest.mark.decision("decision-94"),
]

PREFIX = "plmcp89-"


def server(stack: Stack, token: str | None = None, *toolsets: str) -> FastMCP:
    settings = server_settings(toolsets=list(toolsets or ("core", "tasks")))
    return create_server(paperless_settings(stack.base_url, token or stack.admin_token), settings)


async def run(stack: Stack, tool: str, token: str | None = None, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, tool: str, token: str | None = None, **arguments: object) -> str:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def expected(text: str) -> str:
    """The value a preview says confirms it."""
    [found] = re.findall(r"expected=([0-9-]+)", text)
    return str(found)


def tasks(listed: Json) -> dict[str, Json]:
    return {str(row["task_id"]): row for row in cast("list[Json]", listed["tasks"])}


async def test_admin_has_no_task_tools_and_tasks_has_no_user_tools(stack: Stack) -> None:
    names = {"list_tasks", "acknowledge_tasks", "run_task"}
    async with Client(server(stack, None, "admin")) as client:
        administration = {tool.name for tool in await client.list_tools()}
    async with Client(server(stack, None, "tasks")) as client:
        offered = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint(administration)
    assert "get_user" in administration
    assert names <= offered
    assert "get_user" not in offered


async def test_a_failed_upload_is_found_by_every_filter(stack: Stack) -> None:
    title = f"{PREFIX}{uuid.uuid4().hex}"
    task_id, document = consume(stack.admin, title, b"%PDF-1.4 broken")
    assert document is None
    [wire] = results(stack.admin.get("/api/tasks/", params={"task_id": task_id}))
    me = obj(obj(parse(stack.admin.get("/api/ui_settings/")))["user"])
    created = datetime.fromisoformat(str(wire["date_created"])).isoformat()
    mine: Json = {"name": title}

    matching: list[Json] = [
        {"state": "failure"},
        {"state": ["failure", "revoked"], "is_complete": True},
        {"task_type": "consume_file", "trigger_source": ["api_upload", "web_ui"]},
        {"acknowledged": False, "owner": me["username"]},
        {"owner": me["id"], "result": f"{title}.pdf"},
        {"created_from": created, "created_to": created},
        {"ordering": "-finished"},
    ]
    for arguments in matching:
        found = tasks(await run(stack, "list_tasks", None, **mine, **arguments))
        assert set(found) == {task_id}, arguments
        assert (found[task_id]["file_name"], found[task_id]["owner"]) == (f"{title}.pdf", me["id"])
    others: list[Json] = [
        {"state": "success"},
        {"is_complete": False},
        {"trigger_source": "scheduled"},
        {"acknowledged": True},
    ]
    for arguments in others:
        assert (await run(stack, "list_tasks", None, **mine, **arguments))["count"] == 0, arguments


async def test_a_sanity_check_is_run_listed_and_acknowledged(stack: Stack) -> None:
    started = await run(stack, "run_task", task_type="sanity_check")

    found = None
    for page in range(1, 6):
        listed = await run(
            stack,
            "list_tasks",
            task_type="sanity_check",
            acknowledged=False,
            page=page,
            page_size=100,
        )
        found = next(
            (t for t in cast("list[Json]", listed["tasks"]) if t["task_id"] == started["task_id"]),
            None,
        )
        if found or listed["next_page"] is None:
            break
    assert found is not None
    acknowledged = await run(stack, "acknowledge_tasks", task_ids=[found["id"]])
    assert acknowledged["acknowledged"] == 1


async def test_retraining_is_refused_without_confirm_and_nothing_starts(stack: Stack) -> None:
    before = num(
        (await run(stack, "list_tasks", task_type="train_classifier", page_size=1))["count"]
    )

    text = await refused(stack, "run_task", task_type="train_classifier")

    assert "train the classifier again" in text
    after = num(
        (await run(stack, "list_tasks", task_type="train_classifier", page_size=1))["count"]
    )
    assert after == before


async def test_a_user_without_the_permissions_gets_hints(stack: Stack) -> None:
    listing = await refused(stack, "list_tasks", token=stack.reader_token)
    starting = await refused(stack, "run_task", token=stack.reader_token, task_type="sanity_check")

    assert "view_paperlesstask" in listing
    assert "Only a superuser may start tasks." in starting


async def test_the_overview_counts_a_failed_upload(stack: Stack) -> None:
    title = f"{PREFIX}{uuid.uuid4().hex}"
    task_id, _ = consume(stack.admin, title, b"%PDF-1.4 broken")

    mine = await run(stack, "get_task_overview", None, name=title, days=1)
    everything = await run(stack, "get_task_overview")

    assert cast("Json", mine["counts"]) == {
        "total": 1,
        "needs_attention": 1,
        "in_progress": 0,
        "completed": 0,
    }
    assert (mine["days"], mine["by_type_of"], mine["unavailable"]) == (1, "every_user", [])
    by_type = {str(s["task_type"]): s for s in cast("list[Json]", everything["by_type"])}
    assert num(by_type["consume_file"]["failure"]) >= 1
    assert task_id not in {row["task_id"] for row in cast("list[Json]", everything["active"])}
    assert num(cast("Json", everything["counts"])["total"]) >= 1


async def test_all_tasks_are_acknowledged_after_a_preview(stack: Stack) -> None:
    title = f"{PREFIX}{uuid.uuid4().hex}"
    _ = consume(stack.admin, title, b"%PDF-1.4 broken")

    text = await refused(stack, "acknowledge_tasks", all_unacknowledged=True)
    shown = await run(stack, "acknowledge_tasks", all_unacknowledged=True, dry_run=True)
    before = await run(stack, "list_tasks", None, name=title, acknowledged=False)
    alone = await refused(stack, "acknowledge_tasks", all_unacknowledged=True, confirm=True)
    for _ in range(5):  # a task of Paperless's schedule can come in between
        value = expected(await refused(stack, "acknowledge_tasks", all_unacknowledged=True))
        async with Client(server(stack)) as client:
            result = await client.call_tool(
                "acknowledge_tasks",
                {"all_unacknowledged": True, "confirm": True, "expected": value},
                raise_on_error=False,
            )
        if not result.is_error:
            break
    done = result.structured_content
    assert done is not None, result.content
    after = await run(stack, "list_tasks", None, name=title, acknowledged=True)

    assert f"{title}.pdf"[:40] in text
    assert "cannot be taken back" in text
    assert "cannot be taken back" in alone
    assert expected(text)
    assert (shown["dry_run"], before["count"]) == (True, 1)
    assert num(done["acknowledged"]) >= 1
    assert after["count"] == 1


async def test_the_value_of_a_preview_is_stale_after_another_failure(stack: Stack) -> None:
    first, second = (f"{PREFIX}{uuid.uuid4().hex}" for _ in range(2))
    _ = consume(stack.admin, first, b"%PDF-1.4 broken")
    value = expected(await refused(stack, "acknowledge_tasks", all_unacknowledged=True))
    _ = consume(stack.admin, second, b"%PDF-1.4 broken")

    text = await refused(
        stack, "acknowledge_tasks", all_unacknowledged=True, confirm=True, expected=value
    )

    assert expected(text) != value
    for title in (first, second):
        left = await run(stack, "list_tasks", None, name=title, acknowledged=False)
        assert left["count"] == 1


@pytest.mark.parametrize("kind", ["sanity_check", "train_classifier", "llm_index"])
async def test_every_type_paperless_starts_by_hand_is_started(stack: Stack, kind: str) -> None:
    started = await run(stack, "run_task", task_type=kind, confirm=True)

    ended = await run(stack, "get_task", task_id=started["task_id"], wait_seconds=60)

    assert (ended["task_type"], ended["state"]) == (kind, "success")
