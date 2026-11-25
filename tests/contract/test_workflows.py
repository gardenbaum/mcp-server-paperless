"""Contract of WorkflowReader and WorkflowWriter, on the fake and on a live Paperless.

Workflows a test creates carry the scratch prefix (SCRATCH_WORKFLOW); the live backend deletes
them. A live workflow acts on documents other tests consume, so they are disabled or have a trigger
nothing matches (`never`).
"""

import uuid
from typing import TYPE_CHECKING

import pytest

from contract.conftest import SCRATCH_WORKFLOW
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.domain.workflows import (
    PartWrite,
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
    WorkflowValues,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from contract.conftest import Backend, Ports

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

LIST, GET = "WorkflowReader.list_workflows", "WorkflowReader.get_workflow"
PARTS = "WorkflowWriter.update_workflow_parts"
TRIGGER, ACTION = "WorkflowReader.get_trigger", "WorkflowReader.get_action"
CREATE, UPDATE, DELETE = (
    "WorkflowWriter.create_workflow",
    "WorkflowWriter.update_workflow",
    "WorkflowWriter.delete_workflow",
)
VIEWING = frozenset({"view_workflow", "view_uisettings"})
CHANGING = frozenset({"view_workflow", "add_workflow", "change_workflow", "delete_workflow"})

# A consumption trigger that no file matches, and an action that does nothing harmful.
NEVER = WorkflowTrigger(
    type=1, settings={"sources": [1, 2, 3], "filter_filename": "contract-workflow-never-*.none"}
)
RETITLE = WorkflowAction(type=1, settings={"assign_title": "contract {{ doc_id }}"})


def scratch() -> str:
    return f"{SCRATCH_WORKFLOW}{uuid.uuid4().hex}"


def safe(**given: object) -> WorkflowValues:
    """Values for a disabled workflow with the trigger and action above, unless given."""
    values: dict[str, object] = {"enabled": False, "triggers": (NEVER,), "actions": (RETITLE,)}
    return WorkflowValues.model_validate({**values, **given})


async def named(ports: Ports, name: str) -> Workflow:
    return next(w for w in await ports.workflows.list_workflows() if w.name == name)


@pytest.mark.contract(CREATE)
@pytest.mark.contract(LIST)
async def test_a_created_workflow_keeps_its_settings_and_is_listed(backend: Backend) -> None:
    name = scratch()
    tag = next(t for t in await backend.admin.taxonomy.list_taxonomy("tag") if t.name)
    added = WorkflowTrigger(type=2, settings={"filter_has_tags": [tag.id], "match": "invoice"})
    adding = WorkflowAction(type=1, settings={"assign_title": "x", "assign_tags": [tag.id]})

    made = await backend.admin.workflow_writes.create_workflow(
        name,
        WorkflowValues(order=5, enabled=False, triggers=(NEVER, added), actions=(adding, RETITLE)),
    )

    assert (made.name, made.order, made.enabled) == (name, 5, False)
    assert [t.type for t in made.triggers] == [1, 2]
    assert [a.type for a in made.actions] == [1, 1]
    assert made.triggers[0].settings["filter_filename"] == "contract-workflow-never-*.none"
    assert made.triggers[0].settings["sources"] == [1, 2, 3]
    assert made.triggers[1].settings["filter_has_tags"] == [tag.id]
    assert made.triggers[1].settings["match"] == "invoice"
    assert made.actions[0].settings["assign_tags"] == [tag.id]
    assert made.actions[1].settings["assign_title"] == "contract {{ doc_id }}"
    assert all(item.id is not None for item in (*made.triggers, *made.actions))
    assert await named(backend.admin, name) == made


@pytest.mark.contract(CREATE)
async def test_a_workflow_without_settings_is_enabled_and_has_nothing_to_do(
    backend: Backend,
) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), WorkflowValues())

    assert (made.enabled, made.triggers, made.actions) == (True, (), ())


@pytest.mark.contract(CREATE)
async def test_a_trigger_without_settings_gets_paperless_defaults(backend: Backend) -> None:
    made = await backend.admin.workflow_writes.create_workflow(
        scratch(), safe(triggers=(WorkflowTrigger(type=3),), actions=())
    )

    [trigger] = made.triggers
    assert trigger.type == 3
    assert trigger.settings["sources"] == [1, 2, 3]
    assert trigger.settings["matching_algorithm"] == 0
    assert trigger.settings["match"] == ""
    assert trigger.settings["is_insensitive"] is True
    assert trigger.settings["filter_has_tags"] == []
    assert trigger.settings["filter_has_correspondent"] is None


@pytest.mark.contract(CREATE)
async def test_an_email_and_a_webhook_keep_their_nested_settings(backend: Backend) -> None:
    mail: dict[str, JsonValue] = {
        "subject": "s",
        "body": "b",
        "to": "a@example.org",
        "include_document": True,
    }
    hook: dict[str, JsonValue] = {
        "url": "https://example.org/hook",
        "use_params": False,
        "as_json": True,
        "params": None,
        "body": '{"id": {{ doc_id }}}',
        "headers": {"X-Token": "secret"},
        "include_document": False,
    }
    actions = (
        WorkflowAction(type=3, settings={"email": mail}),
        WorkflowAction(type=4, settings={"webhook": hook}),
        WorkflowAction(type=6),
    )

    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe(actions=actions))

    assert [a.type for a in made.actions] == [3, 4, 6]
    assert made.actions[0].settings["email"] == mail
    assert made.actions[1].settings["webhook"] == hook


@pytest.mark.decision("decision-66")
@pytest.mark.contract(CREATE)
@pytest.mark.contract(UPDATE)
async def test_a_webhook_url_is_stored_as_sent_and_written_back_under_a_new_action_id(
    backend: Backend,
) -> None:
    hook: dict[str, JsonValue] = {"url": "https://user:pw@example.org/hook?a=b@c", "params": {}}
    actions = (WorkflowAction(type=4, settings={"webhook": hook}),)
    writer = backend.admin.workflow_writes
    made = await writer.create_workflow(scratch(), safe(actions=actions))

    again = await writer.update_workflow(made.id, WorkflowValues(actions=actions))

    for workflow in (made, again):
        stored = workflow.actions[0].settings["webhook"]
        assert isinstance(stored, dict)
        assert stored["url"] == hook["url"]
    assert made.actions[0].id != again.actions[0].id


@pytest.mark.contract(CREATE)
async def test_a_scheduled_trigger_keeps_its_schedule(backend: Backend) -> None:
    schedule: dict[str, JsonValue] = {
        "schedule_date_field": "created",
        "schedule_offset_days": -3,
        "schedule_is_recurring": True,
        "schedule_recurring_interval_days": 7,
        "filter_has_tags": [],
        "match": "contract-workflow-never",
        "matching_algorithm": 3,
    }

    made = await backend.admin.workflow_writes.create_workflow(
        scratch(), safe(triggers=(WorkflowTrigger(type=4, settings=schedule),))
    )

    assert made.triggers[0].type == 4
    for key, value in schedule.items():
        assert made.triggers[0].settings[key] == value


@pytest.mark.contract(CREATE)
async def test_a_name_that_is_taken_or_blank_is_refused(backend: Backend) -> None:
    name = scratch()
    writer = backend.admin.workflow_writes
    _ = await writer.create_workflow(name, safe())

    with pytest.raises(Validation):
        _ = await writer.create_workflow(name, safe())
    with pytest.raises(Validation):
        _ = await writer.create_workflow("", safe())


@pytest.mark.contract(CREATE)
async def test_an_unknown_trigger_or_action_type_is_refused_and_nothing_is_made(
    backend: Backend,
) -> None:
    writer = backend.admin.workflow_writes
    name = scratch()

    with pytest.raises(Validation):
        _ = await writer.create_workflow(name, safe(triggers=(WorkflowTrigger(type=99),)))
    with pytest.raises(Validation):
        _ = await writer.create_workflow(name, safe(actions=(WorkflowAction(type=99),)))

    assert [w for w in await backend.admin.workflows.list_workflows() if w.name == name] == []


@pytest.mark.decision("decision-43")
@pytest.mark.contract(CREATE)
@pytest.mark.contract(LIST)
@pytest.mark.contract(UPDATE)
async def test_a_manual_trigger_is_stored_and_kept_by_an_update_of_the_name(
    backend: Backend,
) -> None:
    writer = backend.admin.workflow_writes
    manual = safe(triggers=(WorkflowTrigger(type=5),), actions=())

    made = await writer.create_workflow(scratch(), manual)
    renamed = await writer.update_workflow(made.id, WorkflowValues(name=scratch()))

    assert [t.type for t in made.triggers] == [5]
    assert renamed.triggers == made.triggers
    assert await named(backend.admin, renamed.name) == renamed


@pytest.mark.contract(CREATE)
async def test_a_user_without_the_add_permission_cannot_create(backend: Backend) -> None:
    viewer = backend.restricted(VIEWING)

    with pytest.raises(PermissionDenied):
        _ = await viewer.workflow_writes.create_workflow(scratch(), safe())


@pytest.mark.contract(LIST)
@pytest.mark.contract(GET)
async def test_a_user_without_the_view_permission_is_denied(backend: Backend) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())

    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.workflows.list_workflows()
        with pytest.raises(PermissionDenied):
            _ = await ports.workflows.get_workflow(made.id)


@pytest.mark.contract(LIST)
async def test_workflows_are_listed_by_order_then_id(backend: Backend) -> None:
    writer = backend.admin.workflow_writes
    later = await writer.create_workflow(scratch(), safe(order=20_001))
    first = await writer.create_workflow(scratch(), safe(order=20_000))
    same = await writer.create_workflow(scratch(), safe(order=20_001))

    listed = [w.id for w in await backend.admin.workflows.list_workflows()]

    mine = [i for i in listed if i in {later.id, first.id, same.id}]
    assert mine == [first.id, later.id, same.id]


@pytest.mark.contract(GET)
async def test_one_workflow_is_read_by_id_and_a_missing_one_is_not_found(backend: Backend) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())

    assert await backend.admin.workflows.get_workflow(made.id) == made
    with pytest.raises(NotFound, match="No workflow 999999"):
        _ = await backend.admin.workflows.get_workflow(999_999)


@pytest.mark.contract(GET)
async def test_a_user_with_only_the_view_permission_reads_workflows(backend: Backend) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())
    viewer = backend.restricted(VIEWING)

    assert await viewer.workflows.get_workflow(made.id) == made


@pytest.mark.contract(UPDATE)
async def test_an_update_of_the_name_leaves_triggers_and_actions_alone(backend: Backend) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe(order=3))
    name = scratch()

    changed = await backend.admin.workflow_writes.update_workflow(
        made.id, WorkflowValues(name=name)
    )

    assert changed.name == name
    assert (changed.order, changed.enabled) == (3, False)
    assert changed.triggers == made.triggers
    assert changed.actions == made.actions


@pytest.mark.contract(UPDATE)
async def test_an_update_replaces_a_list_as_a_whole_and_can_empty_it(backend: Backend) -> None:
    made = await backend.admin.workflow_writes.create_workflow(
        scratch(), safe(actions=(RETITLE, WorkflowAction(type=6)))
    )
    writer = backend.admin.workflow_writes
    other = WorkflowAction(type=1, settings={"assign_title": "other"})

    replaced = await writer.update_workflow(made.id, WorkflowValues(actions=(other,)))
    emptied = await writer.update_workflow(made.id, WorkflowValues(triggers=()))

    assert [a.settings["assign_title"] for a in replaced.actions] == ["other"]
    assert replaced.triggers == made.triggers
    assert emptied.triggers == ()
    assert emptied.actions == replaced.actions
    assert await backend.admin.workflows.get_workflow(made.id) == emptied


@pytest.mark.contract(UPDATE)
async def test_an_update_can_switch_a_workflow_on_and_off(backend: Backend) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())
    writer = backend.admin.workflow_writes

    on = await writer.update_workflow(made.id, WorkflowValues(enabled=True))
    off = await writer.update_workflow(made.id, WorkflowValues(enabled=False, order=7))

    assert (on.enabled, off.enabled, off.order) == (True, False, 7)


@pytest.mark.contract(UPDATE)
async def test_an_update_with_an_unknown_type_is_refused_and_changes_nothing(
    backend: Backend,
) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())

    with pytest.raises(Validation):
        _ = await backend.admin.workflow_writes.update_workflow(
            made.id, WorkflowValues(actions=(WorkflowAction(type=99),))
        )

    assert await backend.admin.workflows.get_workflow(made.id) == made


@pytest.mark.contract(UPDATE)
async def test_a_missing_workflow_cannot_be_updated_and_a_viewer_cannot_either(
    backend: Backend,
) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())
    viewer = backend.restricted(VIEWING)

    with pytest.raises(NotFound, match="No workflow 999999"):
        _ = await backend.admin.workflow_writes.update_workflow(999_999, WorkflowValues(name="x"))
    with pytest.raises(PermissionDenied):
        _ = await viewer.workflow_writes.update_workflow(made.id, WorkflowValues(name="x"))


@pytest.mark.contract(DELETE)
async def test_a_deleted_workflow_is_gone(backend: Backend) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())

    await backend.admin.workflow_writes.delete_workflow(made.id)

    with pytest.raises(NotFound):
        _ = await backend.admin.workflows.get_workflow(made.id)
    assert made.id not in [w.id for w in await backend.admin.workflows.list_workflows()]


@pytest.mark.contract(DELETE)
async def test_a_missing_workflow_cannot_be_deleted_and_a_viewer_cannot_either(
    backend: Backend,
) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())
    viewer = backend.restricted(VIEWING)

    with pytest.raises(NotFound, match="No workflow 999999"):
        await backend.admin.workflow_writes.delete_workflow(999_999)
    with pytest.raises(PermissionDenied):
        await viewer.workflow_writes.delete_workflow(made.id)

    assert await backend.admin.workflows.get_workflow(made.id) == made


@pytest.mark.decision("decision-81")
@pytest.mark.contract(LIST)
async def test_the_list_is_filtered_by_enabled_and_by_trigger_type(backend: Backend) -> None:
    writer = backend.admin.workflow_writes
    by_hand = WorkflowTrigger(type=5)
    off = await writer.create_workflow(scratch(), safe())
    on = await writer.create_workflow(scratch(), safe(enabled=True))
    twice = await writer.create_workflow(scratch(), safe(triggers=(by_hand, NEVER, by_hand)))
    mine = {off.id, on.id, twice.id}

    async def listed(*, enabled: bool | None = None, trigger_type: int | None = None) -> list[int]:
        found = await backend.admin.workflows.list_workflows(
            enabled=enabled, trigger_type=trigger_type
        )
        return sorted(w.id for w in found if w.id in mine)

    assert await listed() == [off.id, on.id, twice.id]
    assert await listed(enabled=True) == [on.id]
    assert await listed(enabled=False) == [off.id, twice.id]
    assert await listed(trigger_type=5) == [twice.id]
    assert await listed(trigger_type=1) == [off.id, on.id, twice.id]
    assert await listed(enabled=True, trigger_type=5) == []
    assert await listed(trigger_type=99) == []


@pytest.mark.decision("decision-81")
@pytest.mark.contract(TRIGGER)
@pytest.mark.contract(ACTION)
async def test_a_single_part_is_what_its_workflow_holds(backend: Backend) -> None:
    mail: dict[str, JsonValue] = {"subject": "s", "body": "b", "to": "a@example.org"}
    hook: dict[str, JsonValue] = {
        "url": "https://user:pw@example.org/hook",
        "params": {},
        "headers": {"X-Token": "secret"},
    }
    made = await backend.admin.workflow_writes.create_workflow(
        scratch(),
        safe(
            triggers=(NEVER, WorkflowTrigger(type=5)),
            actions=(
                RETITLE,
                WorkflowAction(type=3, settings={"email": mail}),
                WorkflowAction(type=4, settings={"webhook": hook}),
            ),
        ),
    )
    # The permission for workflows is not needed, the one for the parts is.
    viewer = backend.restricted(frozenset({"view_workflowtrigger", "view_workflowaction"}))

    for ports in (backend.admin, viewer):
        for trigger in made.triggers:
            assert trigger.id is not None
            assert await ports.workflows.get_trigger(trigger.id) == trigger
        for action in made.actions:
            assert action.id is not None
            assert await ports.workflows.get_action(action.id) == action


@pytest.mark.decision("decision-81")
@pytest.mark.contract(TRIGGER)
@pytest.mark.contract(ACTION)
async def test_a_missing_part_is_not_found_and_the_view_permission_is_needed(
    backend: Backend,
) -> None:
    made = await backend.admin.workflow_writes.create_workflow(scratch(), safe())
    trigger, action = made.triggers[0].id, made.actions[0].id
    assert trigger is not None
    assert action is not None
    viewer = backend.restricted(VIEWING)

    with pytest.raises(NotFound, match="No workflow trigger 999999"):
        _ = await backend.admin.workflows.get_trigger(999_999)
    with pytest.raises(NotFound, match="No workflow action 999999"):
        _ = await backend.admin.workflows.get_action(999_999)
    for number in (trigger, 999_999):
        with pytest.raises(PermissionDenied):
            _ = await viewer.workflows.get_trigger(number)
    for number in (action, 999_999):
        with pytest.raises(PermissionDenied):
            _ = await viewer.workflows.get_action(number)


@pytest.mark.decision("decision-81")
@pytest.mark.contract(TRIGGER)
@pytest.mark.contract(ACTION)
async def test_the_parts_of_a_deleted_workflow_stay_until_the_next_update_of_any_workflow(
    backend: Backend,
) -> None:
    writer, reader = backend.admin.workflow_writes, backend.admin.workflows
    other = await writer.create_workflow(scratch(), safe())
    gone = await writer.create_workflow(scratch(), safe())
    trigger, action = gone.triggers[0].id, gone.actions[0].id
    assert trigger is not None
    assert action is not None

    await writer.delete_workflow(gone.id)

    assert await reader.get_trigger(trigger) == gone.triggers[0]
    assert await reader.get_action(action) == gone.actions[0]
    _ = await writer.update_workflow(other.id, WorkflowValues(order=4))
    with pytest.raises(NotFound):
        _ = await reader.get_trigger(trigger)
    with pytest.raises(NotFound):
        _ = await reader.get_action(action)


HOOK: dict[str, JsonValue] = {
    "url": "https://user:pw@example.org/hook",
    "use_params": False,
    "as_json": True,
    "params": None,
    "body": "x",
    "headers": {"X-Token": "secret"},
    "include_document": False,
}
MAIL: dict[str, JsonValue] = {
    "subject": "s",
    "body": "b",
    "to": "a@example.org",
    "include_document": False,
}


def every_action() -> tuple[WorkflowAction, ...]:
    """An action of each type, 5 and 8 included, which this server does not write."""
    suggested: dict[str, JsonValue] = {
        "ai_suggestion_fields": ["title"],
        "ai_create_missing": True,
        "ai_overwrite_existing": True,
    }
    return (
        RETITLE,
        WorkflowAction(type=2, settings={"remove_all_owners": True}),
        WorkflowAction(type=3, settings={"email": MAIL}),
        WorkflowAction(type=4, settings={"webhook": HOOK}),
        WorkflowAction(type=5, settings={"passwords": ["one", "two"]}),
        WorkflowAction(type=6),
        WorkflowAction(type=7),
        WorkflowAction(type=8, settings=suggested),
    )


def kept(part: WorkflowTrigger) -> PartWrite:
    """A trigger as the server sends one that stays: in full."""
    return PartWrite(id=part.id, type=part.type, settings=part.settings)


@pytest.mark.decision("decision-82")
@pytest.mark.contract(PARTS)
async def test_actions_named_by_id_keep_everything_when_one_is_added_moved_or_removed(
    backend: Backend,
) -> None:
    writer, reader = backend.admin.workflow_writes, backend.admin.workflows
    made = await writer.create_workflow(
        scratch(), safe(triggers=(NEVER, WorkflowTrigger(type=5)), actions=every_action())
    )
    parts = {a.id: a for a in made.actions}
    order = [a.id for a in made.actions][::-1]

    await writer.update_workflow_parts(made.id, "actions", [PartWrite(id=n) for n in order])
    assert (await reader.get_workflow(made.id)).actions == tuple(parts[n] for n in order)

    fresh = PartWrite(type=1, settings={"assign_title": "new"})
    ids = [PartWrite(id=n) for n in order]
    await writer.update_workflow_parts(made.id, "actions", [*ids[:2], fresh, *ids[2:]])
    added = (await reader.get_workflow(made.id)).actions
    assert [*added[:2], *added[3:]] == [parts[n] for n in order]
    assert (added[2].type, added[2].settings["assign_title"]) == (1, "new")
    assert added[2].id not in parts

    gone = order.pop(3)
    assert gone is not None
    await writer.update_workflow_parts(made.id, "actions", [PartWrite(id=n) for n in order])
    left = await reader.get_workflow(made.id)
    assert left.actions == tuple(parts[n] for n in order)
    assert left.triggers == made.triggers
    with pytest.raises(NotFound):
        _ = await reader.get_action(gone)


@pytest.mark.decision("decision-82")
@pytest.mark.contract(PARTS)
async def test_a_changed_action_gets_what_is_sent_and_the_others_stay(backend: Backend) -> None:
    writer, reader = backend.admin.workflow_writes, backend.admin.workflows
    made = await writer.create_workflow(
        scratch(), safe(triggers=(NEVER, WorkflowTrigger(type=5)), actions=every_action())
    )
    changes: list[tuple[int, dict[str, JsonValue]]] = [
        (0, {"assign_title": "other", "assign_tags": []}),
        (1, {"remove_all_owners": False}),
        (2, {"email": {**MAIL, "subject": "t"}}),
        (3, {"webhook": {**HOOK, "url": "https://example.org/z", "headers": {"A": "b"}}}),
    ]
    before = list(made.actions)
    for place, settings in changes:
        sent = [PartWrite(id=a.id) for a in before]
        sent[place] = PartWrite(id=before[place].id, settings=settings)

        await writer.update_workflow_parts(made.id, "actions", sent)

        after = list((await reader.get_workflow(made.id)).actions)
        whole = {**before[place].settings, **settings}
        assert after[place] == before[place].model_copy(update={"settings": whole})
        assert after[:place] + after[place + 1 :] == before[:place] + before[place + 1 :]
        before = after


@pytest.mark.decision("decision-82")
@pytest.mark.contract(PARTS)
async def test_triggers_sent_in_full_keep_everything_when_one_is_changed(backend: Backend) -> None:
    writer, reader = backend.admin.workflow_writes, backend.admin.workflows
    tag = next(t for t in await backend.admin.taxonomy.list_taxonomy("tag") if t.name)
    added = WorkflowTrigger(type=2, settings={"filter_has_tags": [tag.id]})
    timed = WorkflowTrigger(type=4, settings={"schedule_offset_days": -3})
    made = await writer.create_workflow(
        scratch(),
        safe(triggers=(NEVER, added, timed, WorkflowTrigger(type=5)), actions=every_action()),
    )
    cleared: dict[str, JsonValue] = {
        **made.triggers[1].settings,
        "filter_has_tags": [],
        "match": "x",
        "matching_algorithm": 1,
    }
    changed = PartWrite(id=made.triggers[1].id, type=2, settings=cleared)
    others = [kept(t) for t in made.triggers]

    await writer.update_workflow_parts(
        made.id, "triggers", [others[0], changed, *others[2:], PartWrite(type=3)]
    )

    after = await reader.get_workflow(made.id)
    by_id = {t.id: t for t in after.triggers}
    assert len(by_id) == 5
    assert by_id[changed.id] == WorkflowTrigger(id=changed.id, type=2, settings=cleared)
    for trigger in (made.triggers[0], *made.triggers[2:]):
        assert by_id[trigger.id] == trigger
    assert after.actions == made.actions


@pytest.mark.decision("decision-82")
@pytest.mark.contract(PARTS)
async def test_paperless_makes_a_part_whose_id_is_gone_and_changes_one_of_another_workflow(
    backend: Backend,
) -> None:
    """Why the server checks ids before it writes and compares afterwards."""
    writer, reader = backend.admin.workflow_writes, backend.admin.workflows
    other = await writer.create_workflow(scratch(), safe())
    gone = await writer.create_workflow(scratch(), safe())
    await writer.delete_workflow(gone.id)
    made = await writer.create_workflow(scratch(), safe())
    _ = await writer.update_workflow(made.id, WorkflowValues(order=1))  # prunes

    ghost = PartWrite(id=gone.actions[0].id)
    theirs = PartWrite(id=other.actions[0].id, settings={"assign_title": "taken"})
    await writer.update_workflow_parts(made.id, "actions", [ghost, theirs])
    lost = PartWrite(id=gone.triggers[0].id)
    await writer.update_workflow_parts(made.id, "triggers", [kept(made.triggers[0]), lost])

    after = await reader.get_workflow(made.id)
    [empty, taken] = after.actions
    assert (empty.id, empty.type, empty.settings["assign_title"]) == (ghost.id, 1, None)
    assert taken.settings["assign_title"] == "taken"
    assert (await reader.get_workflow(other.id)).actions == (taken,)
    matches_all = next(t for t in after.triggers if t.id == lost.id)
    assert (matches_all.type, matches_all.settings["filter_filename"]) == (1, None)
    assert matches_all.settings["sources"] == [1, 2, 3, 4]


@pytest.mark.decision("decision-82")
@pytest.mark.contract(PARTS)
async def test_parts_paperless_refuses_change_nothing(backend: Backend) -> None:
    writer, reader = backend.admin.workflow_writes, backend.admin.workflows
    made = await writer.create_workflow(scratch(), safe(actions=every_action()[:7]))
    viewer = backend.restricted(VIEWING)

    with pytest.raises(Validation, match="Remote OCR"):
        await writer.update_workflow_parts(made.id, "triggers", ())
    with pytest.raises(Validation, match="Remote OCR"):
        # Paperless does not look up the type of a trigger that is sent by its id alone.
        await writer.update_workflow_parts(made.id, "triggers", [PartWrite(id=made.triggers[0].id)])
    with pytest.raises(Validation, match="Email data"):
        await writer.update_workflow_parts(
            made.id, "actions", [PartWrite(id=made.actions[2].id, type=3)]
        )
    with pytest.raises(NotFound, match="No workflow 999999"):
        await writer.update_workflow_parts(999_999, "actions", ())
    with pytest.raises(PermissionDenied):
        await viewer.workflow_writes.update_workflow_parts(made.id, "actions", ())

    assert await reader.get_workflow(made.id) == made


@pytest.mark.decision("decision-82")
@pytest.mark.contract(PARTS)
async def test_the_permission_to_change_workflows_is_enough(backend: Backend) -> None:
    reader = backend.admin.workflows
    tags = await backend.admin.taxonomy.list_taxonomy("tag")
    private = next(t.id for t in tags if t.name == "e2e-private")
    hidden = WorkflowTrigger(type=2, settings={"filter_has_tags": [private]})
    made = await backend.admin.workflow_writes.create_workflow(
        scratch(), safe(triggers=(NEVER, hidden), actions=every_action())
    )
    changer = backend.restricted(frozenset({"view_workflow", "change_workflow"}))

    await changer.workflow_writes.update_workflow_parts(
        made.id, "actions", [PartWrite(id=a.id) for a in made.actions[1:]]
    )
    await changer.workflow_writes.update_workflow_parts(
        made.id, "triggers", [kept(t) for t in made.triggers]
    )

    after = await reader.get_workflow(made.id)
    assert (after.triggers, after.actions) == (made.triggers, made.actions[1:])
