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
    pytest.mark.decision("decision-94"),
]

type Json = dict[str, object]

READS = {"list_tasks", "get_task_overview", "get_system_report"}
TOOLS = READS | {"acknowledge_tasks", "run_task"}


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
    assert await offered("tasks", read_only=True) == READS
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


# --- the overview and acknowledging all (PLMCP-89.2)


async def test_the_overview_is_read_only_and_takes_its_filters() -> None:
    state = seeded()
    at = datetime.now(UTC)
    state.tasks = [
        FakeTask(id=1, task_id="u1", created=at, status="failure", file_name="a.pdf", owner=2),
        FakeTask(
            id=2, task_id="u2", created=at, task_type="mail_fetch", trigger_source="scheduled"
        ),
    ]

    plain = await call(fake(state, writes=False), "get_task_overview")
    for arguments, total in (
        ({"task_type": "mail_fetch"}, 1),
        ({"trigger_source": ["scheduled", "manual"]}, 1),
        ({"owner": "admin", "acknowledged": False}, 1),
        ({"name": "a.pdf", "days": 7}, 1),
        ({"result": "nothing like it"}, 0),
        ({"created_from": "2126-01-01T00:00:00Z"}, 0),
        ({"created_to": "2020-01-01T00:00:00+01:00"}, 0),
    ):
        found = await call(fake(state), "get_task_overview", **arguments)
        assert cast("Json", found["counts"])["total"] == total, arguments

    assert cast("Json", plain["counts"]) == {
        "total": 2,
        "needs_attention": 1,
        "in_progress": 1,
        "completed": 0,
    }
    assert [row["id"] for row in cast("list[Json]", plain["active"])] == [2]
    assert plain["by_type_of"] == "every_user"
    for days in (0, 366):
        _ = await refusal(fake(state), "get_task_overview", days=days)


async def test_acknowledging_all_is_refused_until_confirmed_and_can_be_previewed() -> None:
    state = seeded()
    state.tasks = [FakeTask(id=1, task_id="u1", status="failure", file_name="a.pdf")]

    text = await refusal(fake(state, writes=False), "acknowledge_tasks", all_unacknowledged=True)
    shown = await call(
        fake(state, writes=False), "acknowledge_tasks", all_unacknowledged=True, dry_run=True
    )
    done = await call(fake(state), "acknowledge_tasks", all_unacknowledged=True, confirm=True)

    assert "cannot be taken back" in text
    assert "'a.pdf'" in text
    assert shown["dry_run"] is True
    assert done["acknowledged"] == 1
    assert state.tasks[0].acknowledged


async def test_ids_need_no_confirmation() -> None:
    state = seeded()
    state.tasks = [FakeTask(id=1, task_id="u1")]

    assert (await call(fake(state), "acknowledge_tasks", task_ids=[1]))["acknowledged"] == 1


async def test_run_task_says_what_paperless_starts_and_what_the_index_sends() -> None:
    async with Client(serve(fake(), toolsets=["tasks"])) as client:
        [tool] = [t for t in await client.list_tools() if t.name == "run_task"]
    text = " ".join((tool.description or "").split())

    assert "Paperless starts only these three by hand and one per call; call once per type" in text
    assert "new or changed" in text
    assert "rebuild" not in text


# --- the system report (PLMCP-89.3)


@pytest.mark.decision("decision-95")
async def test_the_system_report_shows_everything_to_who_may_see_it() -> None:
    report = await call(fake(writes=False), "get_system_report")
    restricted = await call(fake(user="e2e-reader"), "get_system_report")

    statistics = cast("Json", report["statistics"])
    status = cast("Json", report["status"])
    assert statistics["documents"] == 2
    assert statistics["file_types"] == [{"mime_type": "application/pdf", "documents": 2}]
    assert cast("Json", status["database"])["type"] == "sqlite"
    assert cast("Json", status["tasks"])["days"] == 30
    assert restricted["status"] is None
    assert cast("Json", restricted["statistics"])["documents"] == 1
