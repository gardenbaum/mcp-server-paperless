"""The workflows toolset through the in-memory client on the seeded fake."""

from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger

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
    "update_workflow_part",
    "run_workflow",
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


async def test_the_workflows_toolset_offers_seven_tools_and_read_only_mode_keeps_two() -> None:
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
        triggers=[
            {
                "type": "added",
                "has_any_correspondents": ["e2e-acme"],
                "match": "invoice",
                "matching_algorithm": "any",
            }
        ],
        actions=[
            {"type": "assignment", "assign_tags": ["e2e-finance"], "assign_title": "{{ title }}"}
        ],
    )

    [trigger] = cast("list[Json]", output["triggers"])
    [action] = cast("list[Json]", output["actions"])
    assert trigger["settings"] == {
        "has_any_correspondents": ["e2e-acme"],
        "match": "invoice",
        "matching_algorithm": "any",
    }
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

    for name in (
        "create_workflow",
        "update_workflow",
        "update_workflow_part",
        "delete_workflow",
    ):
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
    assert hints("update_workflow_part") == (True, False, True)
    assert hints("delete_workflow") == (True, True, False)


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("triggers", [{"type": "added", "filter_has_tags": ["e2e-finance"]}]),
        ("actions", [{"type": "assignment", "assign_tag": ["e2e-finance"]}]),
        (
            "actions",
            [{"type": "webhook", "webhook": {"url": "https://h.example/x", "header": {"a": "b"}}}],
        ),
    ],
)
async def test_a_misspelt_setting_is_an_error_and_nothing_is_created(
    argument: str, value: list[Json]
) -> None:
    state = seeded()

    text = await refusal(fake(state), "create_workflow", name="typo", **{argument: value})

    assert "Extra inputs are not permitted" in text
    assert state.workflows == []


@pytest.mark.decision("decision-66")
async def test_a_read_webhook_written_back_keeps_its_credentials_and_shows_none() -> None:
    state = seeded()
    hook = {"url": "https://user:pw@h.example/x"}
    made = await call(
        fake(state),
        "create_workflow",
        name="Notify",
        enabled=False,
        actions=[{"type": "webhook", "webhook": hook}],
    )
    read = await call(fake(state), "get_workflow", workflow="Notify")
    listed = await call(fake(state), "list_workflows")
    [action] = cast("list[Json]", read["actions"])
    written = {"type": action["type"], "id": action["id"], **cast("Json", action["settings"])}

    updated = await call(fake(state), "update_workflow", workflow="Notify", actions=[written])

    assert action["shown_in_part"] == ["webhook.url"]
    assert all("pw" not in str(output) for output in (made, read, listed, updated))
    assert "Kept as stored, with any credentials" in str(updated["note"])
    stored = state.workflows[0].actions[0].settings["webhook"]
    assert cast("Json", stored)["url"] == hook["url"]


def with_three() -> FakeState:
    """`with_workflow`, a disabled one with a manual trigger and one with a mail rule."""
    state = with_workflow()
    state.workflows += [
        Workflow(
            id=2,
            name="By hand",
            order=1,
            enabled=False,
            triggers=(WorkflowTrigger(id=3, type=5, settings={"filter_has_tags": [4]}),),
            actions=(WorkflowAction(id=4, type=2, settings={"remove_tags": [1]}),),
        ),
        Workflow(
            id=3,
            name="Mail",
            order=2,
            enabled=True,
            triggers=(WorkflowTrigger(id=5, type=1, settings={"filter_mailrule": 7}),),
            actions=(),
        ),
    ]
    return state


def names(output: Json) -> list[object]:
    return [w["name"] for w in cast("list[Json]", output["workflows"])]


@pytest.mark.decision("decision-81")
async def test_the_workflows_that_use_an_object_are_listed_with_the_place() -> None:
    source = fake(with_three(), writes=False)

    output = await call(source, "list_workflows", uses_kind="tag", uses="e2e-finance")
    by_rule = await call(source, "list_workflows", uses_kind="mail_rule", uses=7)

    assert output["count"] == 2
    assert [(w["name"], w["used_in"]) for w in cast("list[Json]", output["workflows"])] == [
        ("Filing", [{"part": "trigger", "id": 1, "setting": "has_tags"}]),
        ("By hand", [{"part": "action", "id": 4, "setting": "remove_tags"}]),
    ]
    assert output["note"] is None
    assert names(by_rule) == ["Mail"]


@pytest.mark.decision("decision-81")
async def test_the_workflows_are_filtered_by_enabled_and_trigger_type() -> None:
    source = fake(with_three(), writes=False)

    assert names(await call(source, "list_workflows", enabled=False)) == ["By hand"]
    assert names(await call(source, "list_workflows", trigger_type="manual")) == ["By hand"]
    assert names(await call(source, "list_workflows", trigger_type="added", enabled=True)) == [
        "Filing"
    ]
    unknown = await refusal(source, "list_workflows", trigger_type="hourly")
    assert "'consumption', 'added', 'updated', 'scheduled' or 'manual'" in unknown


@pytest.mark.decision("decision-81")
@pytest.mark.parametrize("given", [{"uses": "x"}, {"uses_kind": "tag"}], ids=["object", "kind"])
async def test_uses_and_its_kind_go_together(given: Json) -> None:
    text = await refusal(fake(with_three()), "list_workflows", **given)

    assert "uses and uses_kind go together" in text


@pytest.mark.decision("decision-81")
async def test_a_single_trigger_and_action_are_read_with_their_workflows() -> None:
    source = fake(with_three(), writes=False)

    trigger = await call(source, "get_workflow", trigger_id=1)
    action = await call(source, "get_workflow", action_id=4)
    whole = await call(source, "get_workflow", workflow="Filing")

    assert cast("Json", trigger["part"])["trigger"] == {
        "id": 1,
        "type": 2,
        "type_name": "document added",
        "settings": {"has_tags": ["e2e-finance"]},
    }
    assert cast("Json", trigger["part"])["workflows"] == [
        {"id": 1, "name": "Filing", "enabled": True}
    ]
    assert cast("Json", cast("Json", action["part"])["action"])["settings"] == {
        "remove_tags": ["e2e-finance"]
    }
    assert (whole["id"], whole["part"]) == (1, None)
    assert "exactly one" in await refusal(source, "get_workflow")
    assert "exactly one" in await refusal(source, "get_workflow", workflow=1, action_id=4)
    assert "No workflow trigger 99 exists" in await refusal(source, "get_workflow", trigger_id=99)


@pytest.mark.decision("decision-66")
@pytest.mark.decision("decision-81")
async def test_a_single_action_shows_no_hidden_value() -> None:
    state = with_workflow()
    hook = {"url": "https://user:planted@h.example/x", "headers": {"X-Token": "planted"}}
    state.workflows[0] = state.workflows[0].model_copy(
        update={
            "actions": (
                WorkflowAction.model_validate({"id": 8, "type": 4, "settings": {"webhook": hook}}),
                WorkflowAction(id=9, type=5, settings={"passwords": ["planted"]}),
            )
        }
    )
    source = fake(state, writes=False)

    outputs = [
        await call(source, "get_workflow", action_id=8),
        await call(source, "get_workflow", action_id=9),
        await call(source, "list_workflows", uses_kind="tag", uses=1),
    ]

    assert all("planted" not in str(output) for output in outputs)
    shown = cast("Json", cast("Json", outputs[0]["part"])["action"])
    assert shown["shown_in_part"] == ["webhook.url"]


@pytest.mark.decision("decision-82")
async def test_one_action_is_added_changed_and_removed_and_hidden_values_stay() -> None:
    state = with_workflow()
    hook = {"url": "https://user:planted@h.example/x", "headers": {"X-Token": "planted"}}
    secret = WorkflowAction.model_validate({"id": 8, "type": 4, "settings": {"webhook": hook}})
    locked = WorkflowAction(id=9, type=5, settings={"passwords": ["planted"]})
    plain = WorkflowAction(id=2, type=1, settings={"assign_tags": [4]})
    state.workflows[0] = state.workflows[0].model_copy(
        update={"enabled": False, "actions": (plain, secret, locked)}
    )
    state.workflow_object_id[0] = 20
    part = "update_workflow_part"

    added = await call(
        fake(state),
        part,
        workflow="Filing",
        operation="add",
        action={"type": "assignment", "assign_title": "x"},
        position=1,
    )
    changed = await call(
        fake(state),
        part,
        workflow="Filing",
        operation="change",
        action_id=2,
        action={"type": "assignment", "assign_correspondent": "e2e-acme"},
        clear=["assign_tags"],
    )
    text = await refusal(
        fake(state, writes=False), part, workflow="Filing", operation="remove", action_id=8
    )
    removed = await call(
        fake(state), part, workflow="Filing", operation="remove", action_id=8, confirm=True
    )

    assert [a["type"] for a in cast("list[Json]", added["actions"])] == [1, 1, 4, 5]
    assert cast("list[Json]", changed["actions"])[1]["settings"] == {
        "assign_correspondent": "e2e-acme"
    }
    assert "lose for good what is stored hidden: of the webhook action 8 the value of" in text
    assert [a["type"] for a in cast("list[Json]", removed["actions"])] == [1, 1, 5]
    assert state.workflows[0].actions[2] == locked
    assert all("planted" not in str(output) for output in (added, changed, text, removed))


@pytest.mark.decision("decision-82")
async def test_one_trigger_is_changed_and_a_wrong_call_is_an_error() -> None:
    state = with_workflow()
    part = "update_workflow_part"

    output = await call(
        fake(state),
        part,
        workflow=1,
        operation="change",
        trigger_id=1,
        trigger={"type": "added", "filename": None, "match": "x", "matching_algorithm": "any"},
    )
    nothing = await refusal(fake(state), part, workflow=1, operation="change", trigger_id=1)
    unknown = await refusal(fake(state), part, workflow=1, operation="replace", trigger_id=1)
    foreign = await refusal(fake(state), part, workflow=1, operation="remove", trigger_id=77)

    [trigger] = cast("list[Json]", output["triggers"])
    assert trigger["settings"] == {
        "has_tags": ["e2e-finance"],
        "match": "x",
        "matching_algorithm": "any",
    }
    assert "no setting was named" in nothing
    assert "'add', 'change' or 'remove'" in unknown
    assert "holds no trigger 77" in foreign


@pytest.mark.decision("decision-82")
async def test_replacing_a_list_without_its_hidden_values_is_confirmed_while_disabled() -> None:
    state = with_workflow()
    hook = {"url": "https://user:planted@h.example/x", "headers": {"X-Token": "planted"}}
    secret = WorkflowAction.model_validate({"id": 8, "type": 4, "settings": {"webhook": hook}})
    state.workflows[0] = state.workflows[0].model_copy(
        update={"enabled": False, "actions": (secret,)}
    )

    text = await refusal(
        fake(state, writes=False),
        "update_workflow",
        workflow=1,
        actions=[{"type": "assignment", "assign_title": "x"}],
    )

    assert "change the workflow 'Filing' (id 1) and lose for good what is stored hidden" in text
    assert "of the webhook action 8 the value of 'X-Token', the credentials of its URL" in text
    assert "planted" not in text
