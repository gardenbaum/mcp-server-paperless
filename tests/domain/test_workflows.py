"""The workflow tables against the vendored Paperless 3.4.2 schema, and the input models."""

import json
import re
from pathlib import Path
from typing import cast

import pytest

from paperless_mcp.domain.workflows import (
    ACTION_FIELDS,
    ACTION_TYPES,
    MATCHING_ALGORITHMS,
    SCHEDULE_DATE_FIELDS,
    SOURCES,
    TRIGGER_FIELDS,
    TRIGGER_TYPES,
    ActionInput,
    TriggerInput,
    WorkflowValues,
)

SCHEMA = Path(__file__).parents[2] / "docs" / "vendor" / "paperless" / "openapi.json"

type Json = dict[str, object]


def schemas() -> Json:
    document = cast("Json", json.loads(SCHEMA.read_text()))
    return cast("Json", cast("Json", document["components"])["schemas"])


def enum(name: str) -> tuple[list[object], dict[int, str]]:
    """The values of a schema enum and the numbered labels of its description."""
    schema = cast("Json", schemas()[name])
    described = {
        int(number): label
        for number, label in cast(
            "list[tuple[str, str]]",
            re.findall(r"\* `(\d+)` - ([^\n]+)", str(schema["description"])),
        )
    }
    return cast("list[object]", schema["enum"]), described


def properties(name: str) -> set[str]:
    return set(cast("Json", cast("Json", schemas()[name])["properties"]))


@pytest.mark.parametrize(
    ("enum_name", "table"),
    [
        ("WorkflowTriggerTypeEnum", TRIGGER_TYPES),
        ("WorkflowActionTypeEnum", ACTION_TYPES),
        ("SourcesEnum", SOURCES),
        ("WorkflowTriggerMatchingAlgorithmEnum", MATCHING_ALGORITHMS),
    ],
)
def test_the_type_tables_have_the_numbers_of_the_vendored_schema(
    enum_name: str, table: dict[int, str]
) -> None:
    values, _ = enum(enum_name)

    assert sorted(table) == values


@pytest.mark.parametrize(
    ("enum_name", "table"),
    [("WorkflowTriggerTypeEnum", TRIGGER_TYPES), ("WorkflowActionTypeEnum", ACTION_TYPES)],
)
def test_the_type_labels_are_those_of_the_schema_in_lower_case(
    enum_name: str, table: dict[int, str]
) -> None:
    _, described = enum(enum_name)

    assert {n: label.casefold() for n, label in described.items()} == {
        n: label.casefold() for n, label in table.items()
    }


def test_the_schedule_date_fields_are_those_of_the_vendored_schema() -> None:
    values, _ = enum("ScheduleDateFieldEnum")

    assert list(SCHEDULE_DATE_FIELDS) == values


def test_every_setting_of_a_trigger_is_known_and_none_is_missing() -> None:
    wires = {field.wire for field in TRIGGER_FIELDS.values()}

    assert wires == properties("WorkflowTriggerRequest") - {"id", "type"}
    assert len(wires) == len(TRIGGER_FIELDS)


def test_every_setting_of_an_action_is_known_and_the_rest_is_read_only() -> None:
    wires = {field.wire for fields in ACTION_FIELDS.values() for field in fields.values()}
    read_only = {"passwords", "ai_suggestion_fields", "ai_create_missing", "ai_overwrite_existing"}

    assert wires | read_only == properties("WorkflowActionRequest") - {"id", "type"}
    assert not wires & read_only
    assert sum(len(fields) for fields in ACTION_FIELDS.values()) == len(wires)


def test_the_nested_settings_have_the_fields_of_the_schema() -> None:
    assert properties("WorkflowActionEmailRequest") - {"id"} == {
        "subject",
        "body",
        "to",
        "include_document",
    }
    assert properties("WorkflowActionWebhookRequest") - {"id"} == {
        "url",
        "use_params",
        "as_json",
        "params",
        "body",
        "headers",
        "include_document",
    }


def test_the_input_models_have_exactly_the_settings_of_the_tables() -> None:
    triggers = {"type", *TRIGGER_FIELDS}
    actions = {"type", "id", *(name for fields in ACTION_FIELDS.values() for name in fields)}

    assert set(TriggerInput.model_fields) == triggers
    assert set(ActionInput.model_fields) == actions


def test_only_the_settings_that_name_objects_have_a_kind() -> None:
    kinds = {name for name, field in TRIGGER_FIELDS.items() if field.kind}

    assert "has_tags" in kinds
    assert "match" not in kinds
    assert all(field.shape in {"list", "one"} for field in TRIGGER_FIELDS.values() if field.kind)


@pytest.mark.parametrize("name", ["name", "order", "enabled", "triggers", "actions"])
def test_no_field_of_the_values_is_cleared_by_none(name: str) -> None:
    with pytest.raises(ValueError, match=f"{name} cannot be cleared"):
        _ = WorkflowValues.model_validate({name: None})
