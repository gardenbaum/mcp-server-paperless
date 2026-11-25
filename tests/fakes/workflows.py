"""The workflow part of FakePaperless: workflows with nested triggers and actions, as 3.5.1.

Plain functions on the fake's state and user; `FakePaperless` delegates to them (and carries the
write guard), so `paperless.py` does not grow further. What the contract tests pin on live 3.5.1:

- Every user with the global permission of the model sees every workflow (workflows have no owner).
- A trigger or action starts from the defaults of its type and keeps what is sent; a list that is
  sent replaces the old one as a whole and every object in it gets a new id.
- A consumption trigger needs a path, file name or mail rule filter; an e-mail action needs its
  e-mail data, a webhook action a webhook with a URL that has a scheme; remote OCR needs a
  consumption trigger in the workflow. Settings that belong to another type of action are stored
  as sent (Paperless does not check them).
- `update_workflow_parts`: a part sent with an id keeps what is not sent, wherever it is held; an
  id that is gone is made anew (a trigger with the defaults of the model, which match everything),
  and one of another workflow is changed there too. Parts no workflow holds are pruned.
- The triggers and actions of a deleted workflow stay until the next update of any workflow.
- Names are unique and not blank; the list is ordered by `order`, then id.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from fakes.document_values import reject
from mcp_server_paperless.domain.errors import PermissionDenied
from mcp_server_paperless.domain.workflows import (
    ACTION_FIELDS,
    ACTION_TYPES,
    MATCHING_ALGORITHMS,
    SCHEDULE_DATE_FIELDS,
    SOURCES,
    TRIGGER_FIELDS,
    TRIGGER_TYPES,
    Field_,
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
)
from mcp_server_paperless.paperless.workflows import unseen_part, unseen_workflow

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.taxonomy import TaxonomyKind
    from mcp_server_paperless.domain.workflows import PartKind, PartWrite, WorkflowValues

# What 3.4.2 fills in for a trigger and an action that leave a setting out.
TRIGGER_DEFAULTS: dict[str, JsonValue] = {
    "sources": [1, 2, 3],
    "filter_path": None,
    "filter_filename": None,
    "filter_mailrule": None,
    "matching_algorithm": 0,
    "match": "",
    "is_insensitive": True,
    "filter_has_tags": [],
    "filter_has_all_tags": [],
    "filter_has_not_tags": [],
    "filter_custom_field_query": None,
    "filter_has_any_correspondents": [],
    "filter_has_not_correspondents": [],
    "filter_has_any_document_types": [],
    "filter_has_not_document_types": [],
    "filter_has_any_storage_paths": [],
    "filter_has_not_storage_paths": [],
    "filter_has_correspondent": None,
    "filter_has_document_type": None,
    "filter_has_storage_path": None,
    "schedule_offset_days": 0,
    "schedule_is_recurring": False,
    "schedule_recurring_interval_days": 1,
    "schedule_date_field": "added",
    "schedule_date_custom_field": None,
}
ACTION_DEFAULTS: dict[str, JsonValue] = {
    "assign_title": None,
    "assign_tags": [],
    "assign_correspondent": None,
    "assign_document_type": None,
    "assign_storage_path": None,
    "assign_owner": None,
    "assign_view_users": [],
    "assign_view_groups": [],
    "assign_change_users": [],
    "assign_change_groups": [],
    "assign_custom_fields": [],
    "assign_custom_fields_values": {},
    "remove_all_tags": False,
    "remove_tags": [],
    "remove_all_correspondents": False,
    "remove_correspondents": [],
    "remove_all_document_types": False,
    "remove_document_types": [],
    "remove_all_storage_paths": False,
    "remove_storage_paths": [],
    "remove_custom_fields": [],
    "remove_all_custom_fields": False,
    "remove_all_owners": False,
    "remove_owners": [],
    "remove_all_permissions": False,
    "remove_view_users": [],
    "remove_view_groups": [],
    "remove_change_users": [],
    "remove_change_groups": [],
    "email": None,
    "webhook": None,
    "passwords": None,
    "ai_suggestion_fields": None,
    "ai_create_missing": False,
    "ai_overwrite_existing": False,
}
EMAIL_DEFAULTS: dict[str, JsonValue] = {"include_document": False}
WEBHOOK_DEFAULTS: dict[str, JsonValue] = {
    "use_params": True,
    "as_json": False,
    "params": None,
    "body": None,
    "headers": None,
    "include_document": False,
}
CONSUMPTION, SCHEDULED, EMAIL, WEBHOOK, REMOTE_OCR, SUGGEST = 1, 4, 3, 4, 7, 8
MAX_NAME = 256


@dataclass
class WorkflowData:
    """The workflow part of the fake's state (the list is shared with `FakeState`)."""

    workflows: list[Workflow]
    # The last id given to a trigger or action; Paperless numbers them apart, the fake together.
    last_object_id: list[int] = field(default_factory=lambda: [0])
    # The triggers and actions of deleted workflows, until an update prunes them.
    loose_triggers: list[WorkflowTrigger] = field(default_factory=list[WorkflowTrigger])
    loose_actions: list[WorkflowAction] = field(default_factory=list[WorkflowAction])


@dataclass(frozen=True)
class Actor:
    """The acting user as the workflow functions need them."""

    has: Callable[[str], bool]
    known: Callable[[TaxonomyKind], frozenset[int]]


def _allow(user: Actor, action: str, model: str = "workflow") -> None:
    if not user.has(f"{action}_{model}"):
        message = "The Paperless user may not perform this action."
        raise PermissionDenied(message)


def list_workflows(
    data: WorkflowData, user: Actor, *, enabled: bool | None, trigger_type: int | None
) -> tuple[Workflow, ...]:
    """`WorkflowReader.list_workflows`: by order, then id."""
    _allow(user, "view")
    found = [
        workflow
        for workflow in data.workflows
        if enabled in {None, workflow.enabled}
        and (trigger_type is None or any(t.type == trigger_type for t in workflow.triggers))
    ]
    return tuple(sorted(found, key=lambda w: (w.order, w.id)))


def get_workflow(data: WorkflowData, user: Actor, workflow_id: int) -> Workflow:
    """`WorkflowReader.get_workflow`."""
    _allow(user, "view")
    return _find(data, workflow_id)


def get_trigger(data: WorkflowData, user: Actor, trigger_id: int) -> WorkflowTrigger:
    """`WorkflowReader.get_trigger`."""
    _allow(user, "view", "workflowtrigger")
    held = (t for w in data.workflows for t in w.triggers)
    for trigger in (*held, *data.loose_triggers):
        if trigger.id == trigger_id:
            return trigger
    raise unseen_part(kind="trigger", number=trigger_id)


def get_action(data: WorkflowData, user: Actor, action_id: int) -> WorkflowAction:
    """`WorkflowReader.get_action`."""
    _allow(user, "view", "workflowaction")
    held = (a for w in data.workflows for a in w.actions)
    for action in (*held, *data.loose_actions):
        if action.id == action_id:
            return action
    raise unseen_part(kind="action", number=action_id)


def _find(data: WorkflowData, workflow_id: int) -> Workflow:
    found = [w for w in data.workflows if w.id == workflow_id]
    if not found:
        raise unseen_workflow(workflow_id)
    return found[0]


def create_workflow(data: WorkflowData, user: Actor, name: str, values: WorkflowValues) -> Workflow:
    """`WorkflowWriter.create_workflow`."""
    _allow(user, "add")
    _check_name(data, name, except_id=None)
    triggers = _triggers(data, user, values.triggers or ())
    actions = _actions(data, user, values.actions or ())
    _check_together(triggers, actions)
    workflow = Workflow(
        id=max((w.id for w in data.workflows), default=0) + 1,
        name=name,
        order=0 if values.order is None else values.order,
        enabled=True if values.enabled is None else values.enabled,
        triggers=triggers,
        actions=actions,
    )
    data.workflows.append(workflow)
    return workflow


def update_workflow(
    data: WorkflowData, user: Actor, workflow_id: int, values: WorkflowValues
) -> Workflow:
    """`WorkflowWriter.update_workflow` (a partial update)."""
    _allow(user, "change")
    old = _find(data, workflow_id)
    if values.name is not None:
        _check_name(data, values.name, except_id=workflow_id)
    triggers = old.triggers if values.triggers is None else _triggers(data, user, values.triggers)
    actions = old.actions if values.actions is None else _actions(data, user, values.actions)
    _check_together(triggers, actions)
    new = old.model_copy(
        update={
            "name": old.name if values.name is None else values.name,
            "order": old.order if values.order is None else values.order,
            "enabled": old.enabled if values.enabled is None else values.enabled,
            "triggers": triggers,
            "actions": actions,
        }
    )
    data.workflows[data.workflows.index(old)] = new
    data.loose_triggers.clear()
    data.loose_actions.clear()
    return new


# What the model of 3.5.1 fills in for a trigger that is made without a type.
MODEL_TRIGGER: dict[str, JsonValue] = {"sources": [1, 2, 3, 4]}


def update_workflow_parts(
    data: WorkflowData, user: Actor, workflow_id: int, kind: PartKind, parts: Sequence[PartWrite]
) -> None:
    """`WorkflowWriter.update_workflow_parts`."""
    _allow(user, "change")
    old = _find(data, workflow_id)
    _check_sent(old, kind, parts)
    if kind == "triggers":
        known = {t.id: t for w in data.workflows for t in w.triggers}
        known |= {t.id: t for t in data.loose_triggers}
        triggers = tuple(_merged_trigger(data, user, p, known.get(p.id)) for p in parts)
        new = old.model_copy(update={"triggers": tuple(sorted(triggers, key=lambda t: t.id or 0))})
    else:
        held = {a.id: a for w in data.workflows for a in w.actions}
        held |= {a.id: a for a in data.loose_actions}
        actions = tuple(_merged_action(data, user, p, held.get(p.id)) for p in parts)
        new = old.model_copy(update={"actions": actions})
    data.workflows[data.workflows.index(old)] = new
    by_trigger = {t.id: t for t in new.triggers}
    by_action = {a.id: a for a in new.actions}
    data.workflows[:] = [
        w.model_copy(
            update={
                "triggers": tuple(by_trigger.get(t.id, t) for t in w.triggers),
                "actions": tuple(by_action.get(a.id, a) for a in w.actions),
            }
        )
        for w in data.workflows
    ]
    data.loose_triggers.clear()
    data.loose_actions.clear()


def _check_sent(old: Workflow, kind: PartKind, parts: Sequence[PartWrite]) -> None:
    """Paperless looks only at the types that are sent."""
    for part in parts if kind == "actions" else ():
        for number, name in ((EMAIL, "email"), (WEBHOOK, "webhook")):
            if part.type == number and name not in part.settings:
                reject("actions", f"{name.capitalize()} data is required for {name} actions")
    given = [part.type for part in parts]
    triggers = given if kind == "triggers" else [t.type for t in old.triggers]
    actions = given if kind == "actions" else [a.type for a in old.actions]
    if REMOTE_OCR in actions and CONSUMPTION not in triggers:
        reject("non_field_errors", "Remote OCR actions require a consumption started trigger")
    if SUGGEST in actions and not any(t != CONSUMPTION for t in triggers):
        reject(
            "non_field_errors",
            "Apply AI suggestions actions require a trigger other than consumption started",
        )


def _new(part: PartWrite, held: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The settings a part changes (Paperless takes any id, the fake knows the visible ones)."""
    return {k: v for k, v in part.settings.items() if held.get(k) != v}


def _merged_trigger(
    data: WorkflowData, user: Actor, part: PartWrite, held: WorkflowTrigger | None
) -> WorkflowTrigger:
    if part.id is None:
        assert part.type is not None
        [made] = _triggers(data, user, (WorkflowTrigger(type=part.type, settings=part.settings),))
        return made
    base = {**TRIGGER_DEFAULTS, **MODEL_TRIGGER} if held is None else held.settings
    _check_references(user, _new(part, base), _kinds(TRIGGER_FIELDS.values()))
    number = part.type or (CONSUMPTION if held is None else held.type)
    return WorkflowTrigger(id=part.id, type=number, settings={**base, **_once(part.settings)})


def _merged_action(
    data: WorkflowData, user: Actor, part: PartWrite, held: WorkflowAction | None
) -> WorkflowAction:
    if part.id is None:
        assert part.type is not None
        [made] = _actions(data, user, (WorkflowAction(type=part.type, settings=part.settings),))
        return made
    base = ACTION_DEFAULTS if held is None else held.settings
    _check_references(
        user, _new(part, base), _kinds(f for g in ACTION_FIELDS.values() for f in g.values())
    )
    number = part.type or (1 if held is None else held.type)
    return WorkflowAction(id=part.id, type=number, settings={**base, **_once(part.settings)})


def delete_workflow(data: WorkflowData, user: Actor, workflow_id: int) -> None:
    """`WorkflowWriter.delete_workflow`."""
    _allow(user, "delete")
    workflow = _find(data, workflow_id)
    data.workflows.remove(workflow)
    data.loose_triggers.extend(workflow.triggers)
    data.loose_actions.extend(workflow.actions)


def _check_name(data: WorkflowData, name: str, *, except_id: int | None) -> None:
    if not name.strip():
        reject("name", "This field may not be blank.")
    if len(name) > MAX_NAME:
        reject("name", f"Ensure this field has no more than {MAX_NAME} characters.")
    if any(w.name == name and w.id != except_id for w in data.workflows):
        reject("name", "workflow with this name already exists.")


def _next_id(data: WorkflowData) -> int:
    data.last_object_id[0] += 1
    return data.last_object_id[0]


def _once(settings: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The settings as Paperless stores them: texts trimmed, ids and sources without doubles."""
    return {
        name: value.strip()
        if isinstance(value, str)
        else [*dict.fromkeys(cast("list[int]", value))]
        if isinstance(value, list) and all(isinstance(item, int) for item in value)
        else value
        for name, value in settings.items()
    }


def _check_references(
    user: Actor, settings: Mapping[str, JsonValue], fields: Mapping[str, tuple[str, TaxonomyKind]]
) -> None:
    """Every id a setting names must exist ("Invalid pk")."""
    for wire, kind in fields.values():
        value = settings.get(wire)
        ids = value if isinstance(value, list) else [value]
        missing = [i for i in ids if isinstance(i, int) and i not in user.known(kind)]
        if missing:
            reject(wire, f'Invalid pk "{missing[0]}" - object does not exist.')


def _kinds(fields: Iterable[Field_]) -> dict[str, tuple[str, TaxonomyKind]]:
    """The settings that name objects, by Paperless's name, with the kind of object."""
    return {f.wire: (f.wire, f.kind) for f in fields if f.kind is not None}


def _triggers(
    data: WorkflowData, user: Actor, given: tuple[WorkflowTrigger, ...]
) -> tuple[WorkflowTrigger, ...]:
    kinds = _kinds(TRIGGER_FIELDS.values())
    made: list[WorkflowTrigger] = []
    for trigger in given:
        if trigger.type not in TRIGGER_TYPES:
            reject("triggers.type", f'"{trigger.type}" is not a valid choice.')
        settings = {**TRIGGER_DEFAULTS, **_once(trigger.settings)}
        for source in settings["sources"] if isinstance(settings["sources"], list) else []:
            if source not in SOURCES:
                reject("sources", f'"{source}" is not a valid choice.')
        if settings["matching_algorithm"] not in MATCHING_ALGORITHMS:
            reject(
                "matching_algorithm", f'"{settings["matching_algorithm"]}" is not a valid choice.'
            )
        if settings["schedule_date_field"] not in SCHEDULE_DATE_FIELDS:
            reject(
                "schedule_date_field", f'"{settings["schedule_date_field"]}" is not a valid choice.'
            )
        if trigger.type == CONSUMPTION and not any(
            settings[k] for k in ("filter_path", "filter_filename", "filter_mailrule")
        ):
            reject("triggers", "File name, path or mail rule filter are required")
        _check_references(user, settings, kinds)
        made.append(WorkflowTrigger(id=_next_id(data), type=trigger.type, settings=settings))
    return tuple(made)


def _actions(
    data: WorkflowData, user: Actor, given: tuple[WorkflowAction, ...]
) -> tuple[WorkflowAction, ...]:
    kinds = _kinds(f for fields in ACTION_FIELDS.values() for f in fields.values())
    made: list[WorkflowAction] = []
    for action in given:
        if action.type not in ACTION_TYPES:
            reject("actions.type", f'"{action.type}" is not a valid choice.')
        settings = {**ACTION_DEFAULTS, **_once(action.settings)}
        if action.type == EMAIL:
            settings["email"] = _nested(action, "email", EMAIL_DEFAULTS, {"subject", "body", "to"})
        if action.type == WEBHOOK:
            hook = _nested(action, "webhook", WEBHOOK_DEFAULTS, {"url"})
            if "://" not in str(hook["url"]):
                reject("webhook.url", f"Unable to parse URI {hook['url']}, missing scheme")
            settings["webhook"] = hook
        if action.type == SUGGEST:
            _check_suggested(settings.get("ai_suggestion_fields"))
        _check_references(user, settings, kinds)
        made.append(WorkflowAction(id=_next_id(data), type=action.type, settings=settings))
    return tuple(made)


AI_FIELDS = {"title", "tags", "correspondent", "document_type", "storage_path", "created"}


def _check_suggested(fields: JsonValue) -> None:
    if not isinstance(fields, list) or not fields or not {str(f) for f in fields} <= AI_FIELDS:
        reject("actions", "At least one valid field is required for apply AI suggestions actions")


def _nested(
    action: WorkflowAction,
    name: str,
    defaults: dict[str, JsonValue],
    required: set[str],
) -> dict[str, JsonValue]:
    """The e-mail or webhook data of an action, with its defaults (the adapter drops its id)."""
    sent = action.settings.get(name)
    if not isinstance(sent, dict):
        reject("actions", f"{name.capitalize()} data is required for {name} actions")
    for key in sorted(required - set(sent)):
        reject(f"{name}.{key}", "This field is required.")
    return {**defaults, **sent}


def _check_together(
    triggers: tuple[WorkflowTrigger, ...], actions: tuple[WorkflowAction, ...]
) -> None:
    """Remote OCR needs a consumption trigger, AI suggestions another one."""
    if any(a.type == REMOTE_OCR for a in actions) and not any(
        t.type == CONSUMPTION for t in triggers
    ):
        reject("non_field_errors", "Remote OCR actions require a consumption started trigger")
    if any(a.type == SUGGEST for a in actions) and not any(t.type != CONSUMPTION for t in triggers):
        reject(
            "non_field_errors",
            "Apply AI suggestions actions require a trigger other than consumption started",
        )
