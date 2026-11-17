"""Workflows: found by name or id (decision-5), written with taxonomy by name (decision-21).

A workflow runs by itself on every document its triggers match, so what a caller may write is
narrower than what Paperless stores: only the trigger and action types in
``domain.workflows.WRITABLE_*``, and a setting that does not belong to the type it sits under is an
error instead of a value Paperless would store and ignore. Replacing a list never drops what this
server cannot write. Secrets (webhook header values, PDF passwords) are never shown.
"""

import json
import re
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

from paperless_mcp.domain.documents import NameOrId  # noqa: TC001 -- cast target at runtime
from paperless_mcp.domain.errors import (
    ConfirmationRequired,
    PermissionDenied,
    Validation,
    quoted,
)
from paperless_mcp.domain.taxonomy import TaxonomyItem
from paperless_mcp.domain.workflows import (
    ACTION_FIELDS,
    ACTION_TYPES,
    MATCHING_ALGORITHMS,
    SCHEDULE_DATE_FIELDS,
    SOURCES,
    TRIGGER_FIELDS,
    TRIGGER_TYPES,
    WRITABLE_ACTION_TYPES,
    ActionInfo,
    Field_,
    TriggerInfo,
    Workflow,
    WorkflowAction,
    WorkflowDeletion,
    WorkflowInfo,
    WorkflowListing,
    WorkflowTrigger,
    WorkflowValues,
)
from paperless_mcp.services.taxonomy import fold, pick
from paperless_mcp.services.views import plural

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from paperless_mcp.domain.ports import WorkflowReader, WorkflowWriter
    from paperless_mcp.domain.taxonomy import TaxonomyKind
    from paperless_mcp.domain.workflows import (
        ActionInput,
        TriggerInput,
        WorkflowCreate,
        WorkflowEdit,
    )
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# What Paperless shows for a value that was never set; other values are shown. Settings whose
# default is not "empty" are listed here.
NON_EMPTY_DEFAULTS: Final[Mapping[str, JsonValue]] = {
    "sources": [1, 2, 3],
    "is_insensitive": True,
    "schedule_recurring_interval_days": 1,
    "schedule_date_field": "added",
}
HIDDEN: Final = "(hidden)"
# The settings that only a consumption trigger has, and that only a scheduled one has.
CONSUMPTION_ONLY: Final = frozenset({"sources", "path", "filename", "mailrule"})
SCHEDULED_ONLY: Final = frozenset(n for n in TRIGGER_FIELDS if n.startswith("schedule_"))
CONSUMPTION, SCHEDULED, EMAIL, WEBHOOK = 1, 4, 3, 4
# Short names for the types, besides their labels.
TRIGGER_ALIASES: Final[Mapping[int, tuple[str, ...]]] = {
    1: ("consumption",),
    2: ("added",),
    3: ("updated",),
}
ACTION_ALIASES: Final[Mapping[int, tuple[str, ...]]] = {6: ("trash",), 7: ("ocr",)}
# Why a tool may not write an action type that Paperless has.
UNWRITABLE: Final[Mapping[int, str]] = {
    5: "it holds passwords, which this server never shows and so could not keep",
    8: "it makes Paperless ask its AI backend about every matching document",
}
OWNERSHIP: Final = (
    "assign_owner",
    "assign_view_users",
    "assign_view_groups",
    "assign_change_users",
    "assign_change_groups",
)
SUGGESTIONS: Final = 5
# What the actions that need no settings do; password removal and AI suggestions cannot be written
# here but a workflow made in the web UI can hold them (switching it on needs confirm too).
FIXED_ACTS: Final[Mapping[int, str]] = {
    5: "unlock each matching document with stored passwords and add the result as a new version",
    6: "move the matching documents to the trash",
    8: "ask the AI backend about every matching document and apply what it suggests",
}


def _key(text: str) -> str:
    """``text`` folded, without spaces, hyphens and underscores: 'E-Mail' and 'email' alike."""
    return re.sub(r"[\s_-]+", "", fold(text))


def _by_name(
    labels: Mapping[int, str], aliases: Mapping[int, tuple[str, ...]], value: int | str
) -> int | None:
    """The number that ``value`` (a number, digits, a label or a short name) names, or None."""
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        return int(text)
    names = {_key(label): number for number, label in labels.items()}
    names.update({_key(a): number for number, short in aliases.items() for a in short})
    return names.get(_key(text))


def trigger_type(value: int | str) -> int:
    """The trigger type number that ``value`` names.

    Raises:
        Validation: No trigger type has that number or name; the hint lists the types.
    """
    number = _by_name(TRIGGER_TYPES, TRIGGER_ALIASES, value)
    if number in TRIGGER_TYPES:
        return cast("int", number)
    shown = f"{number}" if number is not None else f"is called {str(value).strip()!r}"
    message = f"No workflow trigger type {shown}."
    raise Validation(
        message,
        hint=(
            "Use consumption (1), added (2), updated (3) or scheduled (4), "
            "or a label such as 'document added'."
        ),
    )


def action_type(value: int | str) -> int:
    """The action type number that ``value`` names, if this server may write it.

    Raises:
        Validation: No action type has that number or name, or Paperless has it but this server
            does not write it; the hint says what to do.
    """
    number = _by_name(ACTION_TYPES, ACTION_ALIASES, value)
    if number in WRITABLE_ACTION_TYPES:
        return number
    if number in UNWRITABLE:
        message = (
            f"The workflow action type {ACTION_TYPES[number]!r} ({number}) cannot be written by "
            f"this server: {UNWRITABLE[number]}."
        )
        raise Validation(message, hint="Set it up in the Paperless web UI.")
    shown = f"{number}" if number is not None else f"is called {str(value).strip()!r}"
    message = f"No workflow action type {shown}."
    raise Validation(
        message,
        hint=(
            "Use assignment (1), removal (2), email (3), webhook (4), move to trash (6) or "
            "remote OCR (7)."
        ),
    )


def _lookup(table: Mapping[int, str], what: str, value: int | str) -> int:
    """The key of ``table`` that ``value`` (a number or a name) names, else a Validation."""
    text = str(value).strip()
    wanted = int(text) if text.isascii() and text.isdigit() else None
    if wanted is not None and wanted in table:
        return wanted
    by_name = {_key(name): number for number, name in table.items()}
    if wanted is None and _key(text) in by_name:
        return by_name[_key(text)]
    shown = f"{wanted}" if wanted is not None else f"is called {text!r}"
    names = ", ".join(table.values())
    message = f"No {what} {shown}."
    raise Validation(message, hint=f"Use one of: {names}.")


def _date_field(value: str) -> str:
    if value not in SCHEDULE_DATE_FIELDS:
        message = f"No schedule date field {value!r}."
        raise Validation(message, hint=f"Use {', '.join(SCHEDULE_DATE_FIELDS)}.")
    return value


def _json_text(value: str) -> str:
    try:
        _ = cast("object", json.loads(value))
    except ValueError:
        message = "custom_field_query is not valid JSON."
        raise Validation(message, hint="Pass the query as JSON text.") from None
    return value


# Settings given as text that are checked before they are sent.
TEXT_CHECKS: Final[Mapping[str, Callable[[str], str]]] = {
    "schedule_date_field": _date_field,
    "custom_field_query": _json_text,
}


def _empty(value: JsonValue) -> bool:
    return value is None or value in ([], {}, "", 0, False)


def _is_default(wire: str, value: JsonValue) -> bool:
    if wire in NON_EMPTY_DEFAULTS:
        return value == NON_EMPTY_DEFAULTS[wire]
    return _empty(value)


def _set(fields: Mapping[str, Field_], settings: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The settings that are not at their default, under the names tools use."""
    return {
        name: settings[field.wire]
        for name, field in fields.items()
        if field.wire in settings and not _is_default(field.wire, settings[field.wire])
    }


def _action_fields(action: WorkflowAction) -> Mapping[str, Field_]:
    return ACTION_FIELDS.get(action.type, {})


def _object(value: JsonValue) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", value) if isinstance(value, dict) else {}


def _host(url: JsonValue) -> str:
    """The host of a webhook URL: never its credentials, path or query."""
    text = str(url)
    return urlsplit(text).hostname or text


def _origin(url: JsonValue) -> tuple[str, str | None, int | None]:
    """Scheme, host and port of a URL, as a webhook's destination."""
    parts = urlsplit(str(url))
    return parts.scheme.lower(), parts.hostname, parts.port


def _acts(actions: Sequence[WorkflowAction]) -> list[str]:
    """What the actions do that reaches beyond tagging: the reason a workflow needs confirm."""
    found: list[str] = []
    for action in actions:
        settings = action.settings
        if action.type in FIXED_ACTS:
            found.append(FIXED_ACTS[action.type])
        elif action.type not in ACTION_TYPES:
            found.append(f"run an action of type {action.type}")
        elif action.type == EMAIL:
            mail = _object(settings.get("email"))
            attached = " with the document attached" if mail.get("include_document") else ""
            found.append(f"send an e-mail to {mail.get('to')}{attached}")
        elif action.type == WEBHOOK:
            hook = _object(settings.get("webhook"))
            attached = " with the document" if hook.get("include_document") else ""
            found.append(f"send a POST request to {_host(hook.get('url'))}{attached}")
        elif action.type == 2:  # noqa: PLR2004 -- removal
            removed = [
                name.removeprefix("remove_").replace("_", " ")
                for name in _set(ACTION_FIELDS[2], settings)
            ]
            if removed:
                found.append(f"remove from the matching documents: {', '.join(removed)}")
        elif action.type == 1 and any(not _empty(settings.get(n)) for n in OWNERSHIP):
            found.append("change who owns or may see the matching documents")
    return found


def _trigger_text(trigger: WorkflowTrigger) -> str:
    text = TRIGGER_TYPES.get(trigger.type, f"unknown type {trigger.type}")
    filters = ", ".join(_set(TRIGGER_FIELDS, trigger.settings))
    if filters:
        text += f", filtered by {filters}"
    if trigger.type == SCHEDULED:
        text += ", also on documents that already exist"
    return text


def _effect(lead: str, triggers: Sequence[WorkflowTrigger], acts: Sequence[str]) -> str:
    when = "; ".join(_trigger_text(t) for t in triggers)
    if not triggers:
        when = "no trigger yet: it does nothing until one is added"
    return (
        f"{lead}, which is enabled and runs by itself on every document that matches its "
        f"triggers ({when}) and will: {'; '.join(acts)}"
    )


class WorkflowService:
    """Reads and writes the workflows of Paperless, with triggers and actions."""

    def __init__(
        self, reader: WorkflowReader, writer: WorkflowWriter, taxonomy: TaxonomyResolver
    ) -> None:
        """Read through ``reader``, write through ``writer``, look names up with ``taxonomy``."""
        self._reader: WorkflowReader = reader
        self._writer: WorkflowWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy

    async def list(self) -> WorkflowListing:
        """Every workflow, with its triggers and actions.

        Raises:
            PermissionDenied: The user may not view workflows.
        """
        workflows = await self._reader.list_workflows()
        names = await self._names(workflows)
        return WorkflowListing(
            count=len(workflows), workflows=tuple(self._info(w, names) for w in workflows)
        )

    async def get(self, workflow: NameOrId) -> WorkflowInfo:
        """The workflow that ``workflow`` (a name or an id) names.

        Raises:
            NotFound: No such workflow; the hint lists the closest names.
            Ambiguous: The name matches several workflows.
            PermissionDenied: The user may not view workflows.
        """
        found = await self._find(workflow)
        return self._info(found, await self._names([found]))

    async def create(self, spec: WorkflowCreate, *, confirm: bool) -> WorkflowInfo:
        """Create a workflow; names are resolved and types checked before anything is sent.

        Raises:
            ConfirmationRequired: It is enabled and acts outward or takes things away, and
                ``confirm`` is false. Nothing was changed.
            Validation: The name is empty or taken, or a type, a setting or a value is unusable.
            NotFound: A name matches nothing.
            Ambiguous: A name matches several objects.
            PermissionDenied: The user may not add workflows.
        """
        name = self._name(spec.name)
        await self._free(name, except_id=None)
        triggers = tuple([await self._trigger(t) for t in spec.triggers])
        actions = tuple([await self._action(a, {}) for a in spec.actions])
        acts = _acts(actions)
        if spec.enabled and acts and not confirm:
            raise ConfirmationRequired.for_effect(
                _effect(f"create the workflow {quoted(name)}", triggers, acts)
            )
        fields: dict[str, object] = {
            "enabled": spec.enabled,
            "triggers": triggers,
            "actions": actions,
        }
        if spec.order is not None:
            fields["order"] = spec.order
        made = await self._writer.create_workflow(name, WorkflowValues.model_validate(fields))
        return self._info(made, await self._names([made]))

    async def update(
        self, workflow: NameOrId, edit: WorkflowEdit, *, confirm: bool
    ) -> WorkflowInfo:
        """Change the named settings of a workflow; new triggers or actions replace all old ones.

        Raises:
            ConfirmationRequired: The change leaves an enabled workflow that acts outward or
                takes things away (by new triggers or actions, or by switching it on), and
                ``confirm`` is false. Nothing was changed.
            Validation: Nothing to change, a name that is empty or taken, a setting or value that
                is unusable, or new actions that would drop actions this server cannot write.
            NotFound: No such workflow, or a name matches nothing.
            Ambiguous: A name matches several workflows or objects.
            PermissionDenied: The user may not change workflows.
        """
        found = await self._find(workflow)
        changes: dict[str, object] = {}
        if edit.name is not None:
            changes["name"] = self._name(edit.name)
            await self._free(str(changes["name"]), except_id=found.id)
        if edit.order is not None:
            changes["order"] = edit.order
        if edit.enabled is not None:
            changes["enabled"] = edit.enabled
        if edit.triggers is not None:
            changes["triggers"] = tuple([await self._trigger(t) for t in edit.triggers])
        if edit.actions is not None:
            self._keeps_unwritable(found)
            stored = {a.id: a for a in found.actions if a.id is not None}
            changes["actions"] = tuple([await self._action(a, stored) for a in edit.actions])
        if not changes:
            message = "Nothing to change: no setting was named."
            raise Validation(message, hint="Name a new name, order, enabled, triggers or actions.")
        values = WorkflowValues.model_validate(changes)
        triggers = values.triggers if values.triggers is not None else found.triggers
        actions = values.actions if values.actions is not None else found.actions
        enabled = found.enabled if values.enabled is None else values.enabled
        switched_on = values.enabled is True and not found.enabled
        reshaped = values.triggers is not None or values.actions is not None
        acts = _acts(actions)
        if enabled and acts and (reshaped or switched_on) and not confirm:
            lead = f"change the workflow {quoted(found.name)} (id {found.id})"
            raise ConfirmationRequired.for_effect(_effect(lead, triggers, acts))
        done = await self._writer.update_workflow(found.id, values)
        return self._info(done, await self._names([done]))

    async def delete(self, workflow: NameOrId, *, confirm: bool) -> WorkflowDeletion:
        """Delete a workflow for good; without ``confirm`` refuse and say what deleting it does.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such workflow.
            Ambiguous: The name matches several workflows.
            PermissionDenied: The user may not view or delete workflows.
        """
        found = await self._find(workflow)
        subject = f"the workflow {quoted(found.name)} (id {found.id})"
        if not confirm:
            parts = (
                f"{plural(len(found.triggers), 'trigger')} and "
                f"{plural(len(found.actions), 'action')}"
            )
            effect = (
                f"delete {subject} for good: it stops running at once and its {parts} are lost; "
                "no document is touched"
            )
            raise ConfirmationRequired.for_effect(effect)
        await self._writer.delete_workflow(found.id)
        message = f"{subject[0].upper()}{subject[1:]} was deleted; no document was touched."
        return WorkflowDeletion(id=found.id, name=found.name, message=message)

    async def _find(self, workflow: NameOrId) -> Workflow:
        workflows = await self._reader.list_workflows()
        rows = [TaxonomyItem(id=w.id, name=w.name, document_count=None) for w in workflows]
        wanted = pick("workflow", rows, workflow, listing="list_workflows").id
        return next(w for w in workflows if w.id == wanted)

    @staticmethod
    def _name(name: str) -> str:
        text = name.strip()
        if not text:
            message = "The workflow name is empty."
            raise Validation(message, hint="Give the workflow a name.")
        return text

    async def _free(self, name: str, *, except_id: int | None) -> None:
        """Refuse a name another workflow has (ignoring case): names find workflows."""
        for workflow in await self._reader.list_workflows():
            if workflow.id != except_id and fold(workflow.name) == fold(name):
                message = f"A workflow is already named {quoted(workflow.name)} (id {workflow.id})."
                raise Validation(message, hint="Choose another name, or update that workflow.")

    @staticmethod
    def _keeps_unwritable(found: Workflow) -> None:
        """Refuse to replace actions when that would delete one this server cannot write."""
        kept = [a for a in found.actions if a.type not in WRITABLE_ACTION_TYPES]
        if kept:
            names = ", ".join(
                f"{ACTION_TYPES.get(a.type, f'unknown type {a.type}')} (id {a.id})" for a in kept
            )
            message = (
                f"The workflow {quoted(found.name)} holds actions this server cannot write: "
                f"{names}. Replacing the actions would delete them."
            )
            raise Validation(
                message,
                hint=("Change the other settings, or edit the actions in the Paperless web UI."),
            )

    async def _id(self, kind: TaxonomyKind, value: NameOrId) -> int:
        return (await self._taxonomy.resolve(kind, value)).id

    async def _ids(self, kind: TaxonomyKind, values: Sequence[NameOrId]) -> list[JsonValue]:
        return [await self._id(kind, value) for value in values]

    async def _setting(self, name: str, field: Field_, value: object) -> JsonValue:
        """The stored form of the setting ``name`` given as ``value``."""
        if name == "sources":
            return [_lookup(SOURCES, "source", s) for s in cast("list[int | str]", value)]
        if name == "matching_algorithm":
            return _lookup(MATCHING_ALGORITHMS, "matching algorithm", cast("int | str", value))
        if name in TEXT_CHECKS:
            return TEXT_CHECKS[name](cast("str", value))
        if field.kind is not None and field.shape == "list":
            return await self._ids(field.kind, cast("list[NameOrId]", value))
        if field.kind is not None:
            return await self._id(field.kind, cast("NameOrId", value))
        return cast("JsonValue", value)

    async def _trigger(self, spec: TriggerInput) -> WorkflowTrigger:
        number = trigger_type(spec.type)
        label = TRIGGER_TYPES[number]
        settings: dict[str, JsonValue] = {}
        for name, field in TRIGGER_FIELDS.items():
            value = cast("object", getattr(spec, name))
            if value is None:
                continue
            if name in CONSUMPTION_ONLY and number != CONSUMPTION:
                message = f"{name!r} only applies to consumption triggers, not to {label} triggers."
                raise Validation(message, hint="Leave it out, or use a consumption trigger.")
            if name in SCHEDULED_ONLY and number != SCHEDULED:
                message = f"{name!r} only applies to scheduled triggers, not to {label} triggers."
                raise Validation(message, hint="Leave it out, or use a scheduled trigger.")
            if number == CONSUMPTION and name not in CONSUMPTION_ONLY:
                message = (
                    f"{name!r} does not apply to consumption triggers: no document exists yet."
                )
                raise Validation(message, hint="Use a document added or updated trigger.")
            settings[field.wire] = await self._setting(name, field, value)
        if settings.get("match") and not settings.get("matching_algorithm"):
            message = "A match needs a matching_algorithm other than none to restrict anything."
            raise Validation(
                message,
                hint="Use any, all, exact, regex or fuzzy; with none Paperless ignores the match.",
            )
        return WorkflowTrigger(type=number, settings=settings)

    async def _action(
        self, spec: ActionInput, stored: Mapping[int, WorkflowAction]
    ) -> WorkflowAction:
        number = action_type(spec.type)
        settings: dict[str, JsonValue] = {}
        for owner, fields in ACTION_FIELDS.items():
            for name, field in fields.items():
                value = cast("object", getattr(spec, name))
                if value is None:
                    continue
                if owner != number:
                    message = (
                        f"{name!r} belongs to {ACTION_TYPES[owner]} actions, not to "
                        f"{ACTION_TYPES[number]} actions."
                    )
                    raise Validation(message, hint="Leave it out, or change the action type.")
                settings[field.wire] = await self._action_setting(name, field, value, spec, stored)
        if number == EMAIL and "email" not in settings:
            message = "An email action needs email settings."
            raise Validation(message, hint="Pass email with subject, body and to.")
        if number == WEBHOOK and "webhook" not in settings:
            message = "A webhook action needs webhook settings."
            raise Validation(message, hint="Pass webhook with a url.")
        values = _object(settings.get("assign_custom_fields_values"))
        if values:
            held = cast("list[JsonValue]", settings.get("assign_custom_fields", []))
            settings["assign_custom_fields"] = [
                *held,
                *(int(k) for k in values if int(k) not in held),
            ]
        return WorkflowAction(type=number, settings=settings)

    async def _action_setting(
        self,
        name: str,
        field: Field_,
        value: object,
        spec: ActionInput,
        stored: Mapping[int, WorkflowAction],
    ) -> JsonValue:
        if name == "email":
            return cast("JsonValue", spec.email.model_dump() if spec.email else None)
        if name == "webhook" and spec.webhook is not None:
            hook = spec.webhook.model_dump()
            held = stored.get(spec.id) if spec.id is not None else None
            old = _object(held.settings.get("webhook") if held else None)
            kept = _object(old.get("headers"))
            moved = _origin(old.get("url")) != _origin(spec.webhook.url)
            hook["headers"] = self._headers(spec.webhook.headers, kept, moved=moved)
            return cast("JsonValue", hook)
        if name == "assign_custom_fields_values":
            fields = cast("dict[str, JsonValue]", value)
            return {str(await self._id("custom_field", k)): v for k, v in fields.items()}
        return await self._setting(name, field, value)

    @staticmethod
    def _headers(
        given: Mapping[str, str] | None, kept: Mapping[str, JsonValue], *, moved: bool
    ) -> dict[str, JsonValue] | None:
        """The headers to store: a hidden value stands for the one already stored.

        A stored value is never carried to another scheme, host or port (``moved``): it could be
        a credential, and the change would send it there without anyone seeing it.
        """
        if given is None:
            return None
        headers: dict[str, JsonValue] = {}
        for header, value in given.items():
            if value != HIDDEN:
                headers[header] = value
            elif header in kept and moved:
                message = (
                    f"Header {header!r} cannot keep its stored value: the URL now points to "
                    "another scheme, host or port than the stored one."
                )
                raise Validation(message, hint="Send the real value of the header for the new URL.")
            elif header in kept:
                headers[header] = kept[header]
            else:
                message = (
                    f"Header {header!r} is shown as {HIDDEN!r} and has no stored value to keep."
                )
                raise Validation(
                    message,
                    hint="Send the real value, or the id of the action that holds the stored one.",
                )
        return headers

    async def _names(self, workflows: Sequence[Workflow]) -> dict[TaxonomyKind, dict[int, str]]:
        """The names of the objects the workflows refer to, for the kinds they use."""
        used: set[TaxonomyKind] = set()
        for workflow in workflows:
            for trigger in workflow.triggers:
                used |= _kinds(TRIGGER_FIELDS, trigger.settings)
            for action in workflow.actions:
                used |= _kinds(_action_fields(action), action.settings)
        names: dict[TaxonomyKind, dict[int, str]] = {}
        for kind in used:
            try:
                names[kind] = {item.id: item.name for item in await self._taxonomy.items(kind)}
            except PermissionDenied:
                names[kind] = {}
        return names

    def _info(
        self, workflow: Workflow, names: Mapping[TaxonomyKind, Mapping[int, str]]
    ) -> WorkflowInfo:
        def shown(name: str, field: Field_, value: JsonValue) -> JsonValue:
            return _show(name, field, value, names)

        def trigger(t: WorkflowTrigger) -> TriggerInfo:
            settings = {
                n: shown(n, TRIGGER_FIELDS[n], v)
                for n, v in _set(TRIGGER_FIELDS, t.settings).items()
            }
            label = TRIGGER_TYPES.get(t.type, f"unknown type {t.type}")
            return TriggerInfo(id=t.id, type=t.type, type_name=label, settings=settings)

        def action(a: WorkflowAction) -> ActionInfo:
            fields = _action_fields(a)
            settings = {n: shown(n, fields[n], v) for n, v in _set(fields, a.settings).items()}
            settings.update(_read_only(a))
            label = ACTION_TYPES.get(a.type, f"unknown type {a.type}")
            return ActionInfo(id=a.id, type=a.type, type_name=label, settings=settings)

        return WorkflowInfo(
            id=workflow.id,
            name=workflow.name,
            order=workflow.order,
            enabled=workflow.enabled,
            triggers=tuple(trigger(t) for t in workflow.triggers),
            actions=tuple(action(a) for a in workflow.actions),
        )


def _kinds(fields: Mapping[str, Field_], settings: Mapping[str, JsonValue]) -> set[TaxonomyKind]:
    """The taxonomy kinds that the settings which are set refer to."""
    found: set[TaxonomyKind] = set()
    for name in _set(fields, settings):
        kind = fields[name].kind
        if kind is not None:
            found.add(kind)
    return found


def _read_only(action: WorkflowAction) -> dict[str, JsonValue]:
    """Settings of action types that a tool reads but never writes."""
    settings = action.settings
    if action.type == 5:  # noqa: PLR2004 -- password removal
        return {"passwords": HIDDEN} if not _empty(settings.get("passwords")) else {}
    if action.type == 8:  # noqa: PLR2004 -- apply AI suggestions
        names = ("ai_suggestion_fields", "ai_create_missing", "ai_overwrite_existing")
        return {n: settings[n] for n in names if n in settings and not _empty(settings[n])}
    return {}


def _nested(value: JsonValue) -> dict[str, JsonValue]:
    """An e-mail or webhook as shown: what is set, and header names without their values."""
    shown: dict[str, JsonValue] = {k: v for k, v in _object(value).items() if v is not None}
    if "headers" in shown:
        shown["headers"] = dict.fromkeys(_object(shown["headers"]), HIDDEN)
    return shown


def _show(
    name: str, field: Field_, value: JsonValue, names: Mapping[TaxonomyKind, Mapping[int, str]]
) -> JsonValue:
    """A setting as tools show it: ids as names where known, labels for the numbers."""

    def named(item: JsonValue) -> JsonValue:
        known: Mapping[int, str] = names.get(field.kind, {}) if field.kind else {}
        return known.get(item, item) if isinstance(item, int) else item

    if name == "sources":
        return [
            SOURCES.get(s, s) if isinstance(s, int) else s for s in cast("list[JsonValue]", value)
        ]
    if name == "matching_algorithm":
        return MATCHING_ALGORITHMS.get(cast("int", value), value)
    if name == "assign_custom_fields_values":
        known = names.get("custom_field", {})
        return {
            known.get(int(k), k) if k.isascii() and k.isdigit() else k: v
            for k, v in _object(value).items()
        }
    if name in {"email", "webhook"}:
        return _nested(value)
    if field.kind is not None and field.shape == "list":
        return [named(item) for item in cast("list[JsonValue]", value)]
    return named(value) if field.kind is not None else value
