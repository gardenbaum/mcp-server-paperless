"""The rule type table of saved views against the vendored Paperless schema."""

import json
import re
from pathlib import Path
from typing import cast, get_args

import pytest

from mcp_server_paperless.domain.views import (
    DEFAULT_ICON,
    DISPLAY_FIELDS,
    ICONS,
    RULE_TYPES,
    DisplayMode,
    ViewValues,
    display_field_id,
)

SCHEMA = Path(__file__).parents[2] / "docs" / "vendor" / "paperless" / "openapi.json"

type Json = dict[str, object]


def member(value: object, key: str) -> object:
    assert isinstance(value, dict)
    return cast("Json", value)[key]


def test_the_rule_types_are_those_of_the_vendored_schema() -> None:
    document = cast("object", json.loads(SCHEMA.read_text()))
    enum = member(member(member(document, "components"), "schemas"), "RuleTypeEnum")
    described = {
        int(number): label
        for number, label in cast(
            "list[tuple[str, str]]",
            re.findall(r"\* `(\d+)` - ([^\n]+)", str(member(enum, "description"))),
        )
    }

    assert described == RULE_TYPES
    assert sorted(RULE_TYPES) == member(enum, "enum")


def schema_enum(name: str) -> object:
    document = cast("object", json.loads(SCHEMA.read_text()))
    return member(member(member(member(document, "components"), "schemas"), name), "enum")


def test_the_icons_and_display_modes_are_those_of_the_vendored_schema() -> None:
    assert list(ICONS) == schema_enum("IconEnum")
    assert DEFAULT_ICON in ICONS
    modes = cast("object", DisplayMode.__value__)
    assert list(cast("tuple[str, ...]", get_args(modes))) == schema_enum("DisplayModeEnum")


def test_the_display_fields_are_those_of_paperless_3_5_1() -> None:
    # SavedView.DisplayFields (documents/models.py, v3.5.1); the schema leaves them open.
    assert (
        *("title", "created", "added", "tag", "correspondent", "documenttype", "storagepath"),
        *("note", "owner", "shared", "asn", "pagecount"),
    ) == DISPLAY_FIELDS


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("custom_field_7", 7),
        ("custom_field_10", 10),
        ("custom_field_", None),
        ("custom_field_0", None),
        ("custom_field_07", None),
        ("xcustom_field_3", None),
        ("custom_field_3x", None),
        ("custom_field_3\n", None),
        ("custom_field_\u0663", None),  # an Arabic digit
        ("custom_field_" + "9" * 10, None),
        ("title", None),
    ],
)
def test_only_the_exact_form_names_a_custom_field(text: str, number: int | None) -> None:
    assert display_field_id(text) == number


def test_four_fields_can_be_given_as_none() -> None:
    nothing = {"sort_field", "page_size", "display_mode", "display_fields"}

    values = ViewValues.model_validate(dict.fromkeys(nothing))

    assert values.model_fields_set == nothing


@pytest.mark.parametrize("name", ["name", "filter_rules", "sort_reverse", "icon"])
def test_every_other_field_has_a_value_once_it_is_given(name: str) -> None:
    with pytest.raises(ValueError, match=f"{name} cannot be cleared"):
        _ = ViewValues.model_validate({name: None})
