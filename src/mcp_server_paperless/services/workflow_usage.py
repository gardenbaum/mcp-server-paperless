"""Where a trigger or action of a workflow uses an object, such as a tag or a user.

Paperless has no search for it, so the settings are searched here: every setting that names objects
of the kind, whatever the type of the trigger or action, and the custom fields inside a custom field
query. Not found are names inside templates (titles, e-mails, webhooks), which are free text.
"""

import json
from typing import TYPE_CHECKING, cast

from mcp_server_paperless.domain.workflows import (
    ACTION_FIELDS,
    TRIGGER_FIELDS,
    PartUse,
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from mcp_server_paperless.domain.workflows import Field_, UsedKind

_ATOM = 3  # [field, operator, value]; ["AND", [...]], ["OR", [...]] and ["NOT", ...] have two


def field_uses(query: JsonValue) -> set[JsonValue]:
    """The custom fields, by id or name, of a custom field query (a JSON text)."""
    if not isinstance(query, str):
        return set()
    try:
        left = [cast("JsonValue", json.loads(query))]
    except ValueError, RecursionError:
        return set()
    found: set[JsonValue] = set()
    while left:
        expression = left.pop()
        if not isinstance(expression, list) or not expression:
            continue
        if len(expression) == _ATOM and isinstance(expression[0], int | str):
            found.add(expression[0])
        elif len(expression) == _ATOM - 1:
            operand = expression[1]
            negated = str(expression[0]).lower() == "not"
            left += operand if isinstance(operand, list) and not negated else [operand]
    return found


def _uses(field: Field_, value: JsonValue, number: int, name: str | None) -> bool:
    if field.wire == "filter_custom_field_query":
        return not {number, name}.isdisjoint(field_uses(value))
    if isinstance(value, dict):
        return str(number) in value
    return number in value if isinstance(value, list) else number == value


def _kind(name: str, field: Field_) -> str | None:
    if name == "custom_field_query":
        return "custom_field"
    return "mail_rule" if name == "mailrule" else field.kind


def part_uses(
    part: WorkflowTrigger | WorkflowAction, kind: UsedKind, number: int, name: str | None = None
) -> list[PartUse]:
    """The settings of ``part`` that use the object of ``kind`` with the id ``number``.

    ``name`` is its name, which a custom field query may hold instead of the id.
    """
    trigger = isinstance(part, WorkflowTrigger)
    groups: list[Mapping[str, Field_]] = [TRIGGER_FIELDS] if trigger else [*ACTION_FIELDS.values()]
    return [
        PartUse(part="trigger" if trigger else "action", id=part.id, setting=setting)
        for fields in groups
        for setting, field in fields.items()
        if _kind(setting, field) == kind
        and _uses(field, part.settings.get(field.wire), number, name)
    ]


def workflow_uses(
    workflow: Workflow, kind: UsedKind, number: int, name: str | None
) -> tuple[PartUse, ...]:
    """Like ``part_uses``, over all triggers and actions of ``workflow``."""
    return tuple(
        use
        for part in (*workflow.triggers, *workflow.actions)
        for use in part_uses(part, kind, number, name)
    )
