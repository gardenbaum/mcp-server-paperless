"""Finding the workflows that use an object, and reading single triggers and actions."""

import json
from dataclasses import replace
from typing import TYPE_CHECKING, cast, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, seeded
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.domain.workflows import (
    ACTION_FIELDS,
    ACTION_TYPES,
    TRIGGER_FIELDS,
    PartUse,
    TriggerTypeName,
    Usage,
    UsedKind,
    Workflow,
    WorkflowAction,
    WorkflowHolder,
    WorkflowTrigger,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.workflow_usage import field_uses, part_uses
from mcp_server_paperless.services.workflows import WorkflowService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.workflows import Field_

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-81")]

ADD, EDIT, BY_HAND = 2, 3, 5


def trigger(number: int, kind: int = ADD, **settings: JsonValue) -> WorkflowTrigger:
    return WorkflowTrigger(id=number, type=kind, settings=settings)


def action(number: int, kind: int = 1, **settings: JsonValue) -> WorkflowAction:
    return WorkflowAction(id=number, type=kind, settings=settings)


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, grant: Sequence[str] = ()) -> None:
        self.state = seeded()
        self.state.users = [
            replace(u, permissions=u.permissions | frozenset(grant)) if u.username == user else u
            for u in self.state.users
        ]
        # Without `allow_writes`: reading must not write anything.
        self.fake = FakePaperless(self.state, user=user)
        resolver = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.workflows = WorkflowService(self.fake, self.fake, resolver)

    def seed(
        self,
        name: str,
        triggers: Sequence[WorkflowTrigger] = (),
        actions: Sequence[WorkflowAction] = (),
        *,
        enabled: bool = True,
    ) -> Workflow:
        made = Workflow(
            id=len(self.state.workflows) + 1,
            name=name,
            order=0,
            enabled=enabled,
            triggers=tuple(triggers),
            actions=tuple(actions),
        )
        self.state.workflows.append(made)
        return made

    async def using(self, kind: UsedKind, value: str | int) -> dict[str, tuple[PartUse, ...]]:
        listing = await self.workflows.list(uses=Usage(kind=kind, value=value))
        assert listing.count == len(listing.workflows)
        return {w.name: w.used_in for w in listing.workflows}


def stored(field: Field_, number: int) -> JsonValue:
    if field.shape == "json":
        return {str(number): "x"}
    return [8, number] if field.shape == "list" else number


SETTINGS = [
    *(("trigger", ADD, name, field) for name, field in TRIGGER_FIELDS.items() if field.kind),
    *(
        ("action", kind, name, field)
        for kind, fields in ACTION_FIELDS.items()
        for name, field in fields.items()
        if field.kind
    ),
]


@pytest.mark.parametrize(
    ("part", "kind", "name", "field"), SETTINGS, ids=[f"{s[0]}-{s[2]}" for s in SETTINGS]
)
async def test_every_setting_that_names_an_object_is_searched(
    part: str, kind: int, name: str, field: Field_
) -> None:
    assert field.kind is not None
    assert field.kind != "saved_view"
    made = (trigger if part == "trigger" else action)(7, kind, **{field.wire: stored(field, 1)})

    found = part_uses(made, field.kind, 1)
    other = part_uses(made, field.kind, 2)
    others = [
        k
        for k in ("tag", "correspondent", "document_type", "storage_path", "user", "group")
        if k != field.kind and part_uses(made, k, 1)
    ]

    assert [(use.part, use.id, use.setting) for use in found] == [(part, 7, name)]
    assert other == []
    assert others == []


@pytest.mark.parametrize("kind", sorted(ACTION_TYPES))
async def test_a_setting_stored_under_another_type_is_found_too(kind: int) -> None:
    made = action(3, kind, remove_tags=[4], assign_owner=2)

    assert part_uses(made, "tag", 4) == [PartUse(part="action", id=3, setting="remove_tags")]
    assert part_uses(made, "user", 2) == [PartUse(part="action", id=3, setting="assign_owner")]


async def test_a_mail_rule_is_found_by_its_id() -> None:
    setup = Setup()
    _ = setup.seed("Mail", [trigger(1, 1, filter_mailrule=5), trigger(2, 1, filter_mailrule=6)])
    _ = setup.seed("Five tags", [trigger(3, filter_has_tags=[5])])

    assert await setup.using("mail_rule", 5) == {
        "Mail": (PartUse(part="trigger", id=1, setting="mailrule"),)
    }
    assert await setup.using("mail_rule", " 6 ") == {
        "Mail": (PartUse(part="trigger", id=2, setting="mailrule"),)
    }
    with pytest.raises(Validation, match="A mail rule is given by its id") as raised:
        _ = await setup.using("mail_rule", "invoices")
    assert raised.value.hint == "Call list_mail_rules to see the ids (toolset mail)."


async def test_an_object_is_found_by_its_name_in_every_part_of_every_workflow() -> None:
    setup = Setup()
    _ = setup.seed(
        "Both",
        [trigger(1, filter_has_tags=[1], filter_has_not_tags=[1, 2]), trigger(2)],
        [action(3, assign_tags=[4]), action(4, 2, remove_tags=[1])],
    )
    _ = setup.seed("None", [trigger(5, filter_has_tags=[2])], [action(6)])
    _ = setup.seed("Off", actions=[action(7, assign_tags=[1])], enabled=False)

    assert await setup.using("tag", "E2E-finance") == {
        "Both": (
            PartUse(part="trigger", id=1, setting="has_tags"),
            PartUse(part="trigger", id=1, setting="has_not_tags"),
            PartUse(part="action", id=4, setting="remove_tags"),
        ),
        "Off": (PartUse(part="action", id=7, setting="assign_tags"),),
    }
    assert await setup.using("user", "admin") == {}


async def test_an_unknown_name_is_an_error_and_an_unseen_id_is_searched() -> None:
    setup = Setup(READER[0], grant=("view_workflow",))
    _ = setup.seed("Private", [trigger(1, filter_has_tags=[3])], [action(2, assign_owner=2)])

    with pytest.raises(NotFound, match="No tag is named 'e2e-private'"):
        _ = await setup.using("tag", "e2e-private")
    # The reader may neither see this tag nor list users; the ids stand in the workflow.
    assert list(await setup.using("tag", 3)) == ["Private"]
    assert list(await setup.using("tag", "3")) == ["Private"]
    assert list(await setup.using("user", 2)) == ["Private"]
    with pytest.raises(PermissionDenied):
        _ = await setup.using("user", "admin")


async def test_custom_fields_are_found_in_queries_by_id_and_by_name() -> None:
    setup = Setup()
    nested = ["AND", [["OR", [["x", "exists", True], [1, "exists", True]]], ["y", "exact", "z"]]]
    queries: dict[str, JsonValue] = {
        "by id": json.dumps([1, "exists", True]),
        "by name": json.dumps(["e2e-string", "icontains", "e2e-url"]),
        "nested": json.dumps(nested),
        "negated": json.dumps(["NOT", ["e2e-string", "exists", True]]),
        "value only": json.dumps(["e2e-url", "exact", "e2e-string"]),
        "value list": json.dumps([2, "in", [1, "e2e-string", "e2e-string"]]),
        "another case": json.dumps(["E2E-STRING", "exists", True]),
        "broken": "{",
        "odd": json.dumps({"a": 1}),
        "odd parts": json.dumps(["AND", "x"]),
        "too short": json.dumps([1]),
        "too long": json.dumps(["e2e-string", "e2e-string", "e2e-string", "e2e-string"]),
        "no field": json.dumps([["e2e-string"], "exists", True]),
        "too deep": "[" * 1_000_000,
        "none": None,
    }
    for number, (name, query) in enumerate(queries.items(), start=1):
        _ = setup.seed(name, [trigger(number, filter_custom_field_query=query)])

    found = await setup.using("custom_field", "e2e-string")

    assert list(found) == ["by id", "by name", "nested", "negated"]
    assert found["nested"] == (PartUse(part="trigger", id=3, setting="custom_field_query"),)
    by_id = await setup.using("custom_field", 1)
    assert by_id == found
    assert list(await setup.using("custom_field", 2)) == ["value only", "value list"]


async def test_a_field_that_cannot_be_seen_is_found_by_id_only_and_the_answer_says_so() -> None:
    setup = Setup(grant=())
    _ = setup.seed("By id", [trigger(1, filter_custom_field_query=json.dumps([77, "exists", 1]))])
    _ = setup.seed("By name", [trigger(2, filter_custom_field_query=json.dumps(["n", "a", 1]))])

    listing = await setup.workflows.list(uses=Usage(kind="custom_field", value=77))

    assert [w.name for w in listing.workflows] == ["By id"]
    assert listing.note == (
        "You see no custom field 77: it was searched by its id only, so a custom field query that "
        "names it is not found."
    )
    assert (await setup.workflows.list(uses=Usage(kind="tag", value=77))).note is None
    assert (await setup.workflows.list(uses=Usage(kind="custom_field", value=1))).note is None
    assert (await setup.workflows.list()).note is None


async def test_the_list_is_filtered_by_enabled_and_trigger_type() -> None:
    setup = Setup()
    _ = setup.seed("On", [trigger(1, BY_HAND), trigger(2)], [action(3, assign_tags=[1])])
    _ = setup.seed("Off", [trigger(4, BY_HAND)], enabled=False)
    _ = setup.seed("Edits", [trigger(5, EDIT)])

    async def names(
        *, enabled: bool | None = None, trigger: TriggerTypeName | None = None
    ) -> list[str]:
        listing = await setup.workflows.list(enabled=enabled, trigger=trigger)
        assert all(w.used_in == () for w in listing.workflows)
        return [w.name for w in listing.workflows]

    assert await names() == ["On", "Off", "Edits"]
    assert await names(enabled=True) == ["On", "Edits"]
    assert await names(enabled=False) == ["Off"]
    assert await names(trigger="manual") == ["On", "Off"]
    assert await names(trigger="updated") == ["Edits"]
    assert await names(trigger="consumption") == []
    assert await names(trigger="scheduled") == []
    assert await names(enabled=True, trigger="added") == ["On"]
    both = await setup.workflows.list(enabled=False, uses=Usage(kind="tag", value=1))
    assert both.workflows == ()


async def test_a_single_part_is_shown_with_the_workflows_that_hold_it() -> None:
    setup = Setup()
    hook = {"url": "https://user:pw@h.example/x", "headers": {"X-Token": "planted"}}
    held = action(4, 4, webhook=cast("JsonValue", hook))
    start = trigger(1, filter_has_tags=[1])
    one = setup.seed("One", [start], [held])
    _ = setup.seed("Other", [trigger(2)], [action(5)])
    two = setup.seed("Two", [start], [held], enabled=False)

    part = (await setup.workflows.read(trigger_id=1)).part
    assert part is not None
    assert part.trigger is not None
    assert part.trigger == (await setup.workflows.get("One")).triggers[0]
    assert part.trigger.settings == {"has_tags": ["e2e-finance"]}
    assert part.action is None
    assert part.workflows == (
        WorkflowHolder(id=one.id, name="One", enabled=True),
        WorkflowHolder(id=two.id, name="Two", enabled=False),
    )
    shown = await setup.workflows.read(action_id=4)
    assert shown.part is not None
    assert shown.part.action is not None
    assert shown.part.action == (await setup.workflows.get("One")).actions[0]
    assert shown.part.action.shown_in_part == ("webhook.url",)
    assert shown.part.trigger is None
    assert shown.part.workflows == part.workflows
    assert (shown.id, shown.name, shown.triggers, shown.actions) == (None, None, (), ())
    for secret in ("planted", "pw"):
        assert secret not in shown.model_dump_json()


@pytest.mark.parametrize("kind", [3, 4, 5, 8, 99])
async def test_hidden_values_of_a_single_action_stay_hidden(kind: int) -> None:
    setup = Setup()
    settings: dict[str, JsonValue] = {
        "passwords": ["planted"],
        "webhook": {"url": "https://u:planted@h.example/", "headers": {"A": "planted"}},
        "assign_title": "kept for another type",
    }
    _ = setup.seed("W", actions=[action(1, kind, **settings)])

    shown = await setup.workflows.read(action_id=1)

    assert "planted" not in shown.model_dump_json()


async def test_a_part_no_workflow_holds_does_nothing_and_says_so() -> None:
    setup = Setup()
    setup.state.loose_triggers.append(trigger(9))
    setup.state.loose_actions.append(action(8))

    for shown in (
        await setup.workflows.read(trigger_id=9),
        await setup.workflows.read(action_id=8),
    ):
        assert shown.part is not None
        assert shown.part.workflows == ()


async def test_without_the_permission_for_parts_the_part_is_read_from_the_workflows() -> None:
    setup = Setup(READER[0], grant=("view_workflow",))
    made = setup.seed("W", [trigger(1, filter_filename="x")], [action(2, assign_title="t")])
    setup.state.loose_triggers.append(trigger(9))

    shown = (await setup.workflows.read(trigger_id=1)).part
    done = (await setup.workflows.read(action_id=2)).part

    assert shown is not None
    assert shown.trigger is not None
    assert shown.trigger.settings == {"filename": "x"}
    assert shown.workflows == (WorkflowHolder(id=made.id, name="W", enabled=True),)
    assert done is not None
    assert done.action is not None
    assert done.action.settings == {"assign_title": "t"}
    for read in (
        lambda: setup.workflows.read(trigger_id=9),
        lambda: setup.workflows.read(action_id=9),
    ):
        with pytest.raises(NotFound, match=r"No workflow holds the (trigger|action) 9") as raised:
            _ = await read()
        assert raised.value.hint == "Call list_workflows to see the ids of triggers and actions."


async def test_without_the_permission_for_workflows_the_holders_are_not_known() -> None:
    setup = Setup(READER[0], grant=("view_workflowtrigger", "view_workflowaction"))
    _ = setup.seed("W", [trigger(1)], [action(2)])

    for shown in (
        await setup.workflows.read(trigger_id=1),
        await setup.workflows.read(action_id=2),
    ):
        assert shown.part is not None
        assert shown.part.workflows is None
    with pytest.raises(PermissionDenied):
        _ = await setup.workflows.read(workflow=1)


async def test_without_any_permission_a_part_is_denied() -> None:
    setup = Setup(READER[0])
    _ = setup.seed("W", [trigger(1)], [action(2)])

    with pytest.raises(PermissionDenied):
        _ = await setup.workflows.read(trigger_id=1)
    with pytest.raises(PermissionDenied):
        _ = await setup.workflows.read(action_id=2)


async def test_exactly_one_of_workflow_trigger_and_action_is_read() -> None:
    setup = Setup()
    _ = setup.seed("W", [trigger(1)], [action(2)])

    for chosen in ({}, {"workflow": 1, "trigger_id": 1}, {"trigger_id": 1, "action_id": 2}):
        with pytest.raises(Validation, match="exactly one of workflow, trigger_id and action_id"):
            _ = await setup.workflows.read(**chosen)
    read = await setup.workflows.read(workflow="w")
    assert read.part is None
    assert read.model_dump(exclude={"part"}) == (await setup.workflows.get("W")).model_dump()


class Counting(FakePaperless):
    lists: int = 0

    @override
    async def list_workflows(
        self, *, enabled: bool | None = None, trigger_type: int | None = None
    ) -> tuple[Workflow, ...]:
        self.lists += 1
        return await super().list_workflows(enabled=enabled, trigger_type=trigger_type)


@pytest.mark.parametrize("workflow", [1, "1", " 1 "], ids=["int", "text", "spaces"])
async def test_a_workflow_given_by_id_is_read_alone(workflow: str | int) -> None:
    setup = Setup()
    made = setup.seed("W")
    fake = Counting(setup.state, user=ADMIN[0])
    service = WorkflowService(
        fake, fake, TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=3600.0)
    )

    assert (await service.get(workflow)).name == made.name
    assert fake.lists == 0
    assert (await service.get("w")).id == made.id
    assert fake.lists == 1


KEYS = st.one_of(st.integers(0, 5), st.sampled_from(["f", "g"]))
ATOMS = st.tuples(KEYS, st.just("exists"), st.one_of(st.booleans(), st.lists(KEYS, max_size=3)))
QUERIES = st.recursive(
    ATOMS,
    lambda inner: st.one_of(
        st.tuples(st.sampled_from(["AND", "or"]), st.lists(inner, min_size=1, max_size=3)),
        st.tuples(st.just("not"), inner),
    ),
    max_leaves=6,
)


@given(QUERIES)
def test_the_fields_of_a_query_are_exactly_its_atoms(query: tuple[object, ...]) -> None:
    def keys(expression: tuple[object, ...]) -> set[object]:
        if len(expression) == len(("field", "op", "value")):
            return {expression[0]}
        inner = cast("list[tuple[object, ...]] | tuple[object, ...]", expression[1])
        rows = inner if isinstance(inner, list) else [inner]
        return {k for row in rows for k in keys(row)}

    assert field_uses(json.dumps(query)) == keys(query)
