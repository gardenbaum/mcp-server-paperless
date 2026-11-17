"""The workflows toolset through the in-memory client on the seeded fake."""

from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

from paperless_mcp.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-5"),
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-21"),
]

type Json = dict[str, object]

TOOLS = {
    "list_workflows",
    "get_workflow",
    "create_workflow",
    "update_workflow",
    "delete_workflow",
}
NEVER = {"type": "consumption", "filename": "never-*.pdf"}


def fake(
    state: FakeState | None = None, *, writes: bool = True, user: str = "admin"
) -> FakePaperless:
    return FakePaperless(state or seeded(), user=user, allow_writes=writes)


def with_workflow() -> FakeState:
    state = seeded()
    state.workflows.append(
        Workflow(
            id=1,
            name="Filing",
            order=0,
            enabled=True,
            triggers=(WorkflowTrigger(id=1, type=2, settings={"filter_has_tags": [1]}),),
            actions=(WorkflowAction(id=2, type=1, settings={"assign_tags": [4]}),),
        )
    )
    return state


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(source, toolsets=["core", "workflows"])) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(source, toolsets=["core", "workflows"])) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def test_the_workflows_are_not_in_the_default_profile() -> None:
    async with Client(serve(fake())) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint(TOOLS)


async def test_the_workflows_toolset_offers_five_tools_and_read_only_mode_keeps_two() -> None:
    async with Client(serve(fake(), toolsets=["workflows"])) as client:
        names = {tool.name for tool in await client.list_tools()}
    async with Client(serve(fake(), toolsets=["workflows"], read_only=True)) as client:
        read_only = {tool.name for tool in await client.list_tools()}

    assert names == TOOLS
    assert read_only == {"list_workflows", "get_workflow"}


async def test_the_workflows_are_listed_with_names_instead_of_ids() -> None:
    output = await call(fake(with_workflow()), "list_workflows")

    assert output["count"] == 1
    [workflow] = cast("list[Json]", output["workflows"])
    [trigger] = cast("list[Json]", workflow["triggers"])
    [action] = cast("list[Json]", workflow["actions"])
    assert (workflow["name"], workflow["enabled"]) == ("Filing", True)
    assert (trigger["type_name"], trigger["settings"]) == (
        "document added",
        {"has_tags": ["e2e-finance"]},
    )
    assert (action["type_name"], action["settings"]) == (
        "assignment",
        {"assign_tags": ["e2e-inbox"]},
    )


async def test_a_workflow_is_read_by_name_or_id() -> None:
    by_name = await call(fake(with_workflow()), "get_workflow", workflow="FILING")
    by_id = await call(fake(with_workflow()), "get_workflow", workflow=1)

    assert by_name == by_id


async def test_a_workflow_is_created_with_nested_triggers_and_actions_by_name() -> None:
    state = seeded()

    output = await call(
        fake(state),
        "create_workflow",
        name="Tag invoices",
        triggers=[{"type": "added", "has_any_correspondents": ["e2e-acme"], "match": "invoice"}],
        actions=[
            {"type": "assignment", "assign_tags": ["e2e-finance"], "assign_title": "{{ title }}"}
        ],
    )

    [trigger] = cast("list[Json]", output["triggers"])
    [action] = cast("list[Json]", output["actions"])
    assert trigger["settings"] == {"has_any_correspondents": ["e2e-acme"], "match": "invoice"}
    assert action["settings"] == {"assign_tags": ["e2e-finance"], "assign_title": "{{ title }}"}
    assert [w.name for w in state.workflows] == ["Tag invoices"]


async def test_an_enabled_workflow_that_acts_is_refused_until_confirmed() -> None:
    state = seeded()
    arguments: Json = {
        "name": "Bin it",
        "triggers": [NEVER],
        "actions": [{"type": "move to trash"}],
    }

    text = await refusal(fake(state, writes=False), "create_workflow", **arguments)
    made = await call(fake(state), "create_workflow", confirm=True, **arguments)

    assert "Nothing was changed. Confirming would create the workflow 'Bin it'" in text
    assert "move the matching documents to the trash" in text
    assert "confirm=true" in text
    assert [w.name for w in state.workflows] == ["Bin it"]
    assert made["enabled"] is True


async def test_a_workflow_that_acts_can_be_created_disabled_without_confirm() -> None:
    output = await call(
        fake(),
        "create_workflow",
        name="Later",
        enabled=False,
        triggers=[NEVER],
        actions=[{"type": "email", "email": {"subject": "s", "body": "b", "to": "a@example.org"}}],
    )

    assert output["enabled"] is False


async def test_an_unsupported_action_type_is_an_error_that_says_why() -> None:
    state = seeded()

    text = await refusal(
        fake(state), "create_workflow", name="pw", actions=[{"type": "password removal"}]
    )

    assert "cannot be written by this server" in text
    assert "web UI" in text
    assert state.workflows == []


async def test_a_workflow_is_updated_and_a_hidden_header_survives_it() -> None:
    state = seeded()
    made = await call(
        fake(state),
        "create_workflow",
        name="Notify",
        enabled=False,
        triggers=[NEVER],
        actions=[
            {
                "type": "webhook",
                "webhook": {"url": "https://h.example/x", "headers": {"X-Token": "real"}},
            }
        ],
    )
    [action] = cast("list[Json]", made["actions"])

    output = await call(
        fake(state),
        "update_workflow",
        workflow="Notify",
        name="Notify team",
        actions=[
            {
                "type": "webhook",
                "id": action["id"],
                "webhook": {"url": "https://h.example/y", "headers": {"X-Token": "(hidden)"}},
            }
        ],
    )

    assert output["name"] == "Notify team"
    stored = state.workflows[0].actions[0].settings["webhook"]
    assert cast("Json", stored)["headers"] == {"X-Token": "real"}
    assert "real" not in str(output)


async def test_updating_an_enabled_workflow_to_act_needs_confirm() -> None:
    state = with_workflow()

    text = await refusal(
        fake(state, writes=False),
        "update_workflow",
        workflow="Filing",
        actions=[{"type": "move to trash"}],
    )

    assert "Confirming would change the workflow 'Filing' (id 1)" in text
    assert [a.type for a in state.workflows[0].actions] == [1]


async def test_deleting_without_confirm_is_an_error_that_says_the_effect_and_changes_nothing() -> (
    None
):
    state = with_workflow()

    text = await refusal(fake(state, writes=False), "delete_workflow", workflow="Filing")

    assert "delete the workflow 'Filing' (id 1) for good" in text
    assert "its 1 trigger and 1 action are lost" in text
    assert [w.name for w in state.workflows] == ["Filing"]


async def test_deleting_with_confirm_removes_the_workflow() -> None:
    state = with_workflow()

    output = await call(fake(state), "delete_workflow", workflow=1, confirm=True)

    assert (output["id"], output["name"]) == (1, "Filing")
    assert state.workflows == []


async def test_a_user_without_the_view_permission_gets_a_permission_error() -> None:
    text = await refusal(fake(user="e2e-reader"), "list_workflows")

    assert "may not perform this action" in text


async def test_the_confirm_parameter_is_the_shared_one_and_off_by_default() -> None:
    async with Client(serve(fake(), toolsets=["workflows"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    for name in ("create_workflow", "update_workflow", "delete_workflow"):
        schema = tools[name].input_schema
        properties = cast("dict[str, Json]", schema["properties"])
        assert properties["confirm"]["default"] is False
        assert "confirm" not in cast("list[str]", schema["required"])
        assert "Must be true to go ahead" in str(properties["confirm"]["description"])


async def test_the_annotations_mark_workflows_open_world_and_delete_destructive() -> None:
    async with Client(serve(fake(), toolsets=["workflows"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    def hints(name: str) -> tuple[bool | None, bool | None, bool | None]:
        annotations = tools[name].annotations
        assert annotations is not None
        return (
            annotations.destructive_hint,
            annotations.idempotent_hint,
            annotations.open_world_hint,
        )

    assert hints("create_workflow") == (False, False, True)
    assert hints("update_workflow") == (False, True, True)
    assert hints("delete_workflow") == (True, True, False)
