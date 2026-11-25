"""The tasks toolset through the in-memory client on the seeded fake (PLMCP-89)."""

from datetime import UTC, datetime
from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeState, FakeTask, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.server import CATALOGUE

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-4"),
    pytest.mark.decision("decision-26"),
    pytest.mark.decision("decision-93"),
]

type Json = dict[str, object]

TOOLS = {"list_tasks", "acknowledge_tasks", "run_task"}


def fake(
    state: FakeState | None = None, *, writes: bool = True, user: str = "admin"
) -> FakePaperless:
    return FakePaperless(state or seeded(), user=user, allow_writes=writes)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(source, toolsets=["tasks"])) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(source, toolsets=["tasks"])) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def offered(*toolsets: str, read_only: bool = False) -> set[str]:
    async with Client(serve(fake(), toolsets=list(toolsets), read_only=read_only)) as client:
        return {tool.name for tool in await client.list_tools()}


async def test_the_toolset_offers_the_task_tools_and_admin_no_longer_does() -> None:
    assert await offered("tasks") == TOOLS
    assert await offered("tasks", read_only=True) == {"list_tasks"}
    assert TOOLS.isdisjoint(await offered("admin"))


async def test_the_toolset_is_not_in_the_default_profile() -> None:
    async with Client(serve(fake())) as client:
        assert TOOLS.isdisjoint({tool.name for tool in await client.list_tools()})


def test_the_summaries_of_both_toolsets_say_what_they_are_for() -> None:
    assert CATALOGUE.summaries["tasks"] == "background tasks"
    assert CATALOGUE.summaries["admin"] == "users, groups, settings, logs"


async def test_tasks_are_run_listed_and_acknowledged() -> None:
    state = seeded()
    started = await call(fake(state), "run_task", task_type="sanity_check")

    listed = await call(fake(state), "list_tasks", task_type="sanity_check")
    [row] = cast("list[Json]", listed["tasks"])
    done = await call(fake(state), "acknowledge_tasks", task_ids=[row["id"]])

    assert row["task_id"] == started["task_id"]
    assert done["acknowledged"] == 1


async def test_retraining_needs_confirm() -> None:
    text = await refusal(fake(writes=False), "run_task", task_type="train_classifier")

    assert "train the classifier again" in text


async def test_every_filter_is_an_argument_and_one_value_or_a_list_works() -> None:
    state = seeded()
    at = datetime(2026, 11, 17, 8, tzinfo=UTC)
    state.tasks = [
        FakeTask(id=1, task_id="u1", created=at, status="failure", file_name="a.pdf", owner=2),
        FakeTask(id=2, task_id="u2", created=at, status="revoked", task_type="mail_fetch"),
        FakeTask(id=3, task_id="u3", created=at, status="success", trigger_source="manual"),
    ]
    for arguments, ids in (
        ({"state": "failure"}, [1]),
        ({"state": ["failure", "revoked"]}, [2, 1]),
        ({"task_type": ["mail_fetch"]}, [2]),
        ({"trigger_source": ["api_upload"], "is_complete": True}, [2, 1]),
        ({"owner": "admin"}, [1]),
        ({"name": "A.PDF"}, [1]),
        ({"created_from": "2026-11-17T09:00:00+01:00"}, [3, 2, 1]),
        ({"created_to": "2026-11-17T07:59:59Z"}, []),
        ({"ordering": "state"}, [1, 2, 3]),
    ):
        listed = await call(fake(state), "list_tasks", **arguments)
        assert [row["id"] for row in cast("list[Json]", listed["tasks"])] == ids, arguments


@pytest.mark.parametrize(
    "arguments",
    [
        {"state": "done"},
        {"state": []},
        {"task_type": ["nope"]},
        {"trigger_source": "cron"},
        {"ordering": "owner"},
        {"created_from": "2026-11-17T09:00:00"},
        {"created_to": "yesterday"},
        {"name": ""},
        {"result": "  "},
    ],
)
async def test_what_paperless_does_not_take_is_refused_before_it_is_asked(
    arguments: Json,
) -> None:
    async with Client(serve(fake(), toolsets=["tasks"])) as client:
        result = await client.call_tool("list_tasks", arguments, raise_on_error=False)

    assert result.is_error


async def test_a_user_without_the_permission_gets_a_hint() -> None:
    reader = fake(user="e2e-reader")

    assert "view_paperlesstask" in await refusal(reader, "list_tasks")
    assert "Only a superuser" in await refusal(reader, "run_task", task_type="sanity_check")
