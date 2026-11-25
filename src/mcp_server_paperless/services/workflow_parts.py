"""Adds, changes or removes one trigger or action of a workflow; the others stay (decision-82).

It is written through its workflow: a PATCH of the one list, where the other actions are named by
their ids only (what is hidden, or of a type this server does not write, never leaves Paperless) and
the other triggers are sent back as read. Paperless checks neither ids nor the workflow a part
belongs to, so both are checked here, and the workflow is read again and compared with the plan.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.workflows import (
    ACTION_FIELDS,
    ACTION_TYPES,
    TRIGGER_FIELDS,
    TRIGGER_TYPES,
    WRITABLE_ACTION_TYPES,
    WRITABLE_TRIGGER_TYPES,
    PartWrite,
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
)
from mcp_server_paperless.services.document_runner import refused
from mcp_server_paperless.services.views import plural
from mcp_server_paperless.services.workflows import (
    ASSIGNMENT,
    CONSUMPTION,
    COPY_NOTE,
    NON_EMPTY_DEFAULTS,
    NOT_COPIED,
    REMOTE_OCR,
    SUGGEST,
    action_label,
    action_type,
    as_object,
    copied_places,
    credentials_fate,
    credentials_said,
    far_acts,
    hidden_lost,
    losing,
    past_fate,
    place_notes,
    require_writable_triggers,
    trigger_type,
    workflow_effect,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.documents import NameOrId
    from mcp_server_paperless.domain.ports import WorkflowReader, WorkflowWriter
    from mcp_server_paperless.domain.workflows import (
        ActionInput,
        Field_,
        PartEdit,
        PartKind,
        WorkflowInfo,
    )
    from mcp_server_paperless.services.workflows import WorkflowService

AGAIN: Final = "Read it with get_workflow and tell the user; do not call again on your own."
# One of these a consumption trigger has to have: without any it would match every file.
ORIGIN_FILTERS: Final = ("filter_filename", "filter_path", "filter_mailrule")
ORIGIN_HINT: Final = "Give a path, filename or mailrule filter, or remove the trigger."


@dataclass(frozen=True, slots=True)
class _Plan:
    """A write of one list of ``found`` and what the workflow should hold afterwards.

    Attributes:
        sent: The parts as they are sent.
        triggers: The triggers afterwards; a new one has no id and the settings that were sent.
        actions: The actions afterwards, likewise.
        lead: What is done, for the confirmation; ends before "the workflow".
        said: By place, what a confirmation says of the stored credentials of a webhook.
        losses: Which hidden values are lost.
        notes: What the result says.
    """

    found: Workflow
    kind: PartKind
    sent: tuple[PartWrite, ...]
    triggers: tuple[WorkflowTrigger, ...]
    actions: tuple[WorkflowAction, ...]
    lead: str
    said: Mapping[int, str]
    losses: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _usage(message: str, hint: str) -> Validation:
    return Validation(f"Nothing was changed: {message}", hint=hint)


def _check(edit: PartEdit) -> None:
    """Refuse arguments that do not belong together.

    Raises:
        Validation: What is missing or too much.
    """
    given = [edit.trigger, edit.action].count(None)
    ids = [edit.trigger_id, edit.action_id].count(None)
    if edit.operation == "add":
        if given != 1 or ids != 2 or edit.clear:  # noqa: PLR2004 -- neither id
            message = "add takes exactly one of trigger and action, without an id and clear."
            raise _usage(message, "Pass the new trigger or action.")
    elif ids != 1:
        message = f"{edit.operation} takes exactly one of trigger_id and action_id."
        raise _usage(message, "Call get_workflow to see the ids.")
    elif edit.operation == "remove":
        if given != 2 or edit.clear or edit.position is not None:  # noqa: PLR2004 -- neither
            message = "remove takes an id and nothing else."
            raise _usage(message, "Leave out trigger, action, clear and position.")
    else:
        _check_change(edit)
    if edit.position is not None and (edit.trigger or edit.trigger_id is not None):
        message = "triggers have no order."
        raise _usage(message, "Leave out position.")
    if edit.action is not None and edit.action.id not in {None, edit.action_id}:
        message = "the action has another id than action_id."
        raise _usage(message, "Leave out the id inside action.")


def _check_change(edit: PartEdit) -> None:
    """Refuse a change of nothing, or of the other kind than its id.

    Raises:
        Validation: What is wrong.
    """
    wrong = edit.trigger if edit.trigger_id is None else edit.action
    if wrong is not None:
        message = "a trigger goes with trigger_id, an action with action_id."
        raise _usage(message, "Pass the id of what is to be changed.")
    if not (edit.trigger or edit.action or edit.clear) and edit.position is None:
        message = "no setting was named."
        raise _usage(message, "Pass the settings that change, clear or position.")


def _part[T: (WorkflowTrigger, WorkflowAction)](
    parts: Sequence[T], number: int, label: str, found: Workflow
) -> T:
    for part in parts:
        if part.id == number:
            return part
    message = f"The workflow {quoted(found.name)} (id {found.id}) holds no {label} {number}."
    raise NotFound(message, hint="Call get_workflow to see the ids of its triggers and actions.")


def _cleared(
    names: Sequence[str], fields: Mapping[str, Field_], given: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """The settings ``names`` at their default, under Paperless's names.

    Raises:
        Validation: A name is no setting that can be cleared, or it is given as well.
    """
    cleared: dict[str, JsonValue] = {}
    for name in names:
        field = fields.get(name)
        if field is None or name in {"email", "webhook"}:
            allowed = ", ".join(n for n in fields if n not in {"email", "webhook"}) or "none"
            message = f"{name!r} cannot be cleared."
            raise _usage(message, f"What can be: {allowed}.")
        if field.wire in given:
            message = f"{name!r} is given and cleared."
            raise _usage(message, "Do one of them.")
        empty: dict[str, JsonValue] = {"list": [], "flag": False, "json": {}}
        value = empty.get(field.shape)
        if name in {"matching_algorithm", "schedule_offset_days"}:
            value = 0
        elif name == "match":
            value = ""
        cleared[field.wire] = NON_EMPTY_DEFAULTS.get(field.wire, value)
    return cleared


def _same_type(number: int, stored: int, labels: Mapping[int, str], part: str) -> None:
    """Refuse another type than the stored one.

    Raises:
        Validation: The types differ.
    """
    if number != stored:
        message = (
            f"the {part} is of type {labels.get(stored, f'unknown type {stored}')!r}, not "
            f"{labels[number]!r}; a change does not change the type."
        )
        raise _usage(message, "Remove it and add another one.")


def _stays(message: str) -> Validation:
    return _usage(message, "Change it in the Paperless web UI; it can be moved and removed here.")


def _broken(triggers: Sequence[WorkflowTrigger], actions: Sequence[WorkflowAction]) -> str:
    """Which rule on triggers and actions together is broken ("" if none is)."""
    types = {a.type for a in actions}
    if REMOTE_OCR in types and all(t.type != CONSUMPTION for t in triggers):
        return "remote OCR needs a consumption trigger in the workflow."
    if SUGGEST in types and all(t.type == CONSUMPTION for t in triggers):
        return "applying AI suggestions needs a trigger other than consumption in the workflow."
    return ""


def _normal(value: JsonValue) -> JsonValue:
    """``value`` as Paperless may store it: without spaces around, and lists in any order."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        rows: list[JsonValue] = [_normal(item) for item in value]
        return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)) or None
    if isinstance(value, dict):
        return {k: _normal(v) for k, v in value.items()} or None
    return value


def _unlike(wanted: Mapping[str, JsonValue], now: Mapping[str, JsonValue]) -> list[str]:
    """The settings in ``wanted`` that ``now`` holds differently."""
    found: list[str] = []
    for name, value in wanted.items():
        there = now.get(name)
        if name in {"email", "webhook"} and isinstance(value, dict):
            inner = as_object(there)
            found += [f"{name}.{k}" for k in _unlike(value, inner)]
        elif _normal(value) != _normal(there):
            found.append(name)
    return found


def _compared(
    label: str,
    wanted: Sequence[WorkflowTrigger] | Sequence[WorkflowAction],
    now: Sequence[WorkflowTrigger] | Sequence[WorkflowAction],
    *,
    ordered: bool,
) -> list[str]:
    """How a list of parts differs from the plan; setting names only, never a value."""
    wrong: list[str] = []
    known = {part.id for part in wanted}
    fresh = [part for part in now if part.id not in known]
    by_id = {part.id: part for part in now}
    expected: list[int | None] = []
    for part in wanted:
        there = by_id.get(part.id) if part.id is not None else next(iter(fresh), None)
        name = f"the new {label}" if part.id is None else f"{label} {part.id}"
        if there is None:
            wrong.append(f"{name} is missing")
            continue
        expected.append(there.id)
        if there.type != part.type:
            wrong.append(f"{name} is of type {there.type}")
        if settings := _unlike(part.settings, there.settings):
            wrong.append(f"{name} has another {', '.join(settings)}")
    extra = [part.id for part in now if part.id not in expected]
    wrong += [f"{label} {number} was not planned" for number in extra]
    if ordered and not wrong and [part.id for part in now] != expected:
        wrong.append(f"the {label}s are in another order")
    return wrong


def _differences(plan: _Plan, now: Workflow) -> list[str]:
    return [
        *_compared("trigger", plan.triggers, now.triggers, ordered=False),
        *_compared("action", plan.actions, now.actions, ordered=True),
    ]


def _holding(workflows: Sequence[Workflow], found: Workflow, kind: PartKind, number: int) -> str:
    """The other workflows that hold a part, for a message ("" if none does)."""
    others = [
        w
        for w in workflows
        if w.id != found.id
        and any(p.id == number for p in (w.triggers if kind == "triggers" else w.actions))
    ]
    return ", ".join(f"{quoted(w.name)} (id {w.id})" for w in others)


class WorkflowParts:
    """Adds, changes and removes single triggers and actions of workflows."""

    def __init__(
        self, reader: WorkflowReader, writer: WorkflowWriter, workflows: WorkflowService
    ) -> None:
        """Read and write through the ports; ``workflows`` knows settings, names and results."""
        self._reader: WorkflowReader = reader
        self._writer: WorkflowWriter = writer
        self._workflows: WorkflowService = workflows

    async def update(self, workflow: NameOrId, edit: PartEdit, *, confirm: bool) -> WorkflowInfo:
        """Add, change or remove one trigger or action; the others stay as they are.

        Raises:
            ConfirmationRequired: The workflow that results is enabled and acts, or hidden values
                are lost, and ``confirm`` is false. Nothing was changed.
            Validation: The arguments do not belong together, or a setting is unusable.
            NotFound: No such workflow, or it does not hold the part.
            Ambiguous: The name matches several workflows or objects.
            PermissionDenied: The user may not change workflows.
            PaperlessMcpError: What was written is not what was planned, or is not known.
        """
        _check(edit)
        workflows = await self._reader.list_workflows()
        found = self._workflows.pick_from(workflows, workflow)
        kind: PartKind = "actions" if edit.action or edit.action_id is not None else "triggers"
        if kind == "actions":
            plan = await self._plan_actions(workflows, found, edit)
            self._keeps_order_elsewhere(workflows, plan)
        else:
            plan = await self._plan_triggers(workflows, found, edit)
        if (why := _broken(plan.triggers, plan.actions)) and not _broken(
            found.triggers, found.actions
        ):
            raise _usage(why, "Add or keep such a trigger, or remove the action.")
        self._ask(plan, confirm=confirm)
        return await self._write(plan)

    @staticmethod
    def _ask(plan: _Plan, *, confirm: bool) -> None:
        acts = far_acts(plan.actions, plan.said) if plan.found.enabled else []
        if not (acts or plan.losses) or confirm:
            return
        lead = f"{plan.lead} the workflow {quoted(plan.found.name)} (id {plan.found.id})"
        if plan.losses:
            lead += f" and {losing(plan.losses)}"
        effect = workflow_effect(lead, plan.triggers, acts) if acts else lead
        ConfirmationRequired.require(effect, scope=(plan.found, plan.triggers, plan.actions))

    # --- plans

    async def _plan_triggers(
        self, workflows: Sequence[Workflow], found: Workflow, edit: PartEdit
    ) -> _Plan:
        triggers = list(found.triggers)
        if edit.trigger is not None:
            require_writable_triggers([edit.trigger])
        if edit.operation == "add" and edit.trigger is not None:
            new = await self._workflows.build_trigger(edit.trigger)
            self._limited(new)
            triggers.append(new)
            lead = f"add a {TRIGGER_TYPES[new.type]!r} trigger to"
        else:
            number = edit.trigger_id or 0
            old = _part(found.triggers, number, "trigger", found)
            label = f"the {TRIGGER_TYPES.get(old.type, f'type {old.type}')!r} trigger {old.id}"
            if edit.operation == "remove":
                triggers.remove(old)
                lead = f"remove {label} from"
            else:
                new = await self._changed_trigger(old, edit)
                if others := _holding(workflows, found, "triggers", number):
                    raise self._shared(label, others)
                triggers[triggers.index(old)] = new
                lead = f"change {label} of"
        return _Plan(
            found=found,
            kind="triggers",
            sent=tuple(PartWrite(id=t.id, type=t.type, settings=t.settings) for t in triggers),
            triggers=tuple(triggers),
            actions=found.actions,
            lead=lead,
            said={},
            notes=() if triggers else ("It has no trigger now: it does nothing.",),
        )

    async def _changed_trigger(self, old: WorkflowTrigger, edit: PartEdit) -> WorkflowTrigger:
        if old.type not in WRITABLE_TRIGGER_TYPES:
            message = f"a trigger of type {TRIGGER_TYPES.get(old.type, old.type)!r} is not written."
            raise _stays(message)
        given: Mapping[str, JsonValue] = {}
        cleared = _cleared(edit.clear, TRIGGER_FIELDS, {})
        if edit.trigger is not None:
            _same_type(trigger_type(edit.trigger.type), old.type, TRIGGER_TYPES, "trigger")
            held = {**old.settings, **cleared}
            given = (await self._workflows.build_trigger(edit.trigger, held)).settings
        cleared = _cleared(edit.clear, TRIGGER_FIELDS, given)
        new = old.model_copy(update={"settings": {**old.settings, **cleared, **given}})
        self._limited(new)
        return new

    @staticmethod
    def _limited(trigger: WorkflowTrigger) -> None:
        """Refuse a consumption trigger that would match every file.

        Raises:
            Validation: It has no path, file name or mail rule filter.
        """
        if trigger.type == CONSUMPTION and not any(
            trigger.settings.get(name) for name in ORIGIN_FILTERS
        ):
            message = "a consumption trigger needs a path, filename or mailrule filter."
            raise _usage(message, ORIGIN_HINT)

    async def _plan_actions(
        self, workflows: Sequence[Workflow], found: Workflow, edit: PartEdit
    ) -> _Plan:
        if edit.operation == "add" and edit.action is not None:
            return await self._plan_new_action(workflows, found, edit, edit.action)
        number = edit.action_id or 0
        old = _part(found.actions, number, "action", found)
        others = _holding(workflows, found, "actions", number)
        actions = [a for a in found.actions if a.id != number]
        place = _place(edit, found.actions.index(old) + 1, len(found.actions))
        label = f"the {action_label(old)}"
        if edit.operation == "remove":
            lost = [] if others else hidden_lost(old, None)
            return _Plan(
                found=found,
                kind="actions",
                sent=tuple(PartWrite(id=a.id) for a in actions),
                triggers=found.triggers,
                actions=tuple(actions),
                lead=f"remove {label} from",
                said={},
                losses=(f"of {label} {', '.join(lost)}",) if lost else (),
                notes=() if actions else ("It has no action now: it does nothing.",),
            )
        sent, new = PartWrite(id=old.id), old
        said: Mapping[int, str] = {}
        losses: tuple[str, ...] = ()
        notes: list[str] = []
        lead = f"move {label} of"
        if edit.action is not None or edit.clear:
            if others:
                raise self._shared(label, others)
            settings = await self._changed_action(found, old, edit, place)
            sent = PartWrite(id=old.id, settings=settings)
            new = old.model_copy(update={"settings": {**old.settings, **settings}})
            lead = f"change {label} of"
            lost_hidden = hidden_lost(old, new, credentials=not found.enabled)
            losses = (f"of {label} {', '.join(lost_hidden)}",) if lost_hidden else ()
            if edit.action is not None:
                said, notes = self._credentials(
                    workflows, found, edit.action.model_copy(update={"id": old.id}), old, new, place
                )
        actions.insert(place - 1, new)
        return _Plan(
            found=found,
            kind="actions",
            sent=tuple(sent if a.id == number else PartWrite(id=a.id) for a in actions),
            triggers=found.triggers,
            actions=tuple(actions),
            lead=lead,
            said=said,
            losses=losses,
            notes=tuple(notes),
        )

    async def _plan_new_action(
        self, workflows: Sequence[Workflow], found: Workflow, edit: PartEdit, spec: ActionInput
    ) -> _Plan:
        place = _place(edit, len(found.actions) + 1, len(found.actions) + 1)
        new = await self._workflows.build_action(spec, found.actions, place)
        copies = copied_places([spec], [new], workflows, found)
        actions = list(found.actions)
        actions.insert(place - 1, new)
        return _Plan(
            found=found,
            kind="actions",
            sent=tuple(
                PartWrite(type=new.type, settings=new.settings) if a is new else PartWrite(id=a.id)
                for a in actions
            ),
            triggers=found.triggers,
            actions=tuple(actions),
            lead=f"add {_article(ACTION_TYPES[new.type])} action to",
            said={place: f", {NOT_COPIED}"} if copies else {},
            notes=tuple(place_notes(COPY_NOTE, [place] if copies else [])),
        )

    async def _changed_action(
        self, found: Workflow, old: WorkflowAction, edit: PartEdit, place: int
    ) -> dict[str, JsonValue]:
        """The settings of an action that change, under Paperless's names."""
        if old.type not in WRITABLE_ACTION_TYPES:
            message = f"an action of type {ACTION_TYPES.get(old.type, old.type)!r} is not written."
            raise _stays(message)
        given: dict[str, JsonValue] = {}
        fields = ACTION_FIELDS.get(old.type, {})
        if edit.action is not None:
            _same_type(action_type(edit.action.type), old.type, ACTION_TYPES, "action")
            spec = edit.action.model_copy(update={"id": old.id})
            given = dict((await self._workflows.build_action(spec, found.actions, place)).settings)
            if not given and not edit.clear:
                message = "no setting was named."
                raise _usage(message, "Pass the settings that change, clear or position.")
            values = as_object(given.get("assign_custom_fields_values"))
            if old.type == ASSIGNMENT and values and spec.assign_custom_fields is None:
                # Paperless writes only the values of fields in assign_custom_fields.
                held = old.settings.get("assign_custom_fields")
                fields_now = list(held) if isinstance(held, list) else []
                more = [int(k) for k in values if int(k) not in fields_now]
                given["assign_custom_fields"] = [*fields_now, *more]
        return {**_cleared(edit.clear, fields, given), **given}

    @staticmethod
    def _credentials(  # noqa: PLR0913, PLR0917 -- an action before and after, and where it stands
        workflows: Sequence[Workflow],
        found: Workflow,
        spec: ActionInput,
        old: WorkflowAction,
        new: WorkflowAction,
        place: int,
    ) -> tuple[Mapping[int, str], list[str]]:
        """What confirmation and result say of the credentials in the URL of a changed webhook."""
        alone = found.model_copy(update={"actions": (old,)})
        kept, lost, _ = credentials_fate([spec], [new], alone)
        copies = copied_places([spec], [new], workflows, alone)
        notes = place_notes(COPY_NOTE, [place] if copies else [])
        fate = lost.get(1, "")
        if fate:
            notes += place_notes(past_fate(fate), [place])
        notes += place_notes("Kept as stored, with any credentials", [place] if kept else [])
        said = credentials_said({place: fate} if fate else {}, copies and [place])
        return said, notes

    @staticmethod
    def _shared(label: str, others: str) -> Validation:
        message = f"other workflows hold {label}, too: {others}. A change would change them all."
        return _usage(message, "Change it in the Paperless web UI, or remove it here and add one.")

    @staticmethod
    def _keeps_order_elsewhere(workflows: Sequence[Workflow], plan: _Plan) -> None:
        """Refuse a write that could reorder the actions of another workflow.

        The order is a setting of the action itself, so an action in two workflows takes its place
        with it.

        Raises:
            Validation: An action that stays is held by another workflow with several actions.
        """
        several = [w for w in workflows if len(w.actions) > 1]
        for action in plan.actions:
            if action.id is None:
                continue
            others = _holding(several, plan.found, "actions", action.id)
            if others:
                message = (
                    f"other workflows hold the {action_label(action)}, too: {others}. Writing "
                    "the actions could change their order there."
                )
                raise _usage(message, "Change it in the Paperless web UI.")

    # --- writing

    async def _write(self, plan: _Plan) -> WorkflowInfo:
        """Send the plan once, read the workflow and compare."""
        lost: PaperlessMcpError | None = None
        try:
            await self._writer.update_workflow_parts(plan.found.id, plan.kind, plan.sent)
        except PaperlessMcpError as error:
            if refused(error):
                raise
            lost = error
        try:
            now = await self._reader.get_workflow(plan.found.id)
        except PaperlessMcpError as error:
            start = "The change was written" if lost is None else lost.message
            known = "it could not be checked" if lost is None else "whether it was is not known"
            message = f"{start}; {known}: {error.message}"
            raise PaperlessMcpError(message, hint=AGAIN) from None
        wrong = _differences(plan, now)
        if not wrong:
            return await self._workflows.answer(now, plan.notes)
        if lost is not None:
            unchanged = (now.triggers, now.actions) == (plan.found.triggers, plan.found.actions)
            state = (
                "the workflow is as it was, so it was not written yet"
                if unchanged
                else f"the workflow is not as planned: {_listed(wrong)}"
            )
            message = f"{lost.message} Read again: {state}."
            raise PaperlessMcpError(message, hint=AGAIN) from None
        message = (
            f"The change was written, but the workflow is not as planned: {_listed(wrong)}. "
            "Someone else may have changed it at the same time."
        )
        raise PaperlessMcpError(message, hint=AGAIN)


def _listed(wrong: Sequence[str]) -> str:
    return "; ".join(wrong)


def _article(label: str) -> str:
    return f"{'an' if label[0] in 'aeiou' else 'a'} {label}"


def _place(edit: PartEdit, default: int, last: int) -> int:
    """The place of the action, from 1.

    Raises:
        Validation: There is no such place.
    """
    place = default if edit.position is None else edit.position
    if not 1 <= place <= last:
        message = f"there is no position {place}: the workflow has {plural(last, 'place')}."
        raise _usage(message, f"Use 1 to {last}.")
    return place
