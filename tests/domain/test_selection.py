"""The filters a selection takes: strict, and the same as a search."""

from datetime import UTC, date, datetime
from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError

from mcp_server_paperless.domain.documents import DocumentQuery, NameFilters
from mcp_server_paperless.domain.selection import (
    DocumentRow,
    DocumentsRead,
    ReadField,
    ReadRow,
    SelectionFilters,
)

pytestmark = pytest.mark.decision("decision-69")


def test_an_unknown_filter_is_refused() -> None:
    with pytest.raises(ValidationError, match="tag_all"):
        _ = SelectionFilters.model_validate({"tag_all": ["x"]})


def test_every_filter_becomes_its_own_name_or_part_of_the_query() -> None:
    filters = SelectionFilters.model_validate(
        {
            "query": "acme",
            "more_like_id": 4,
            "title_contains": "t",
            "correspondent": "c",
            "document_type": "d",
            "storage_path": "s",
            "has_correspondent": True,
            "has_document_type": False,
            "has_storage_path": True,
            "has_tags": False,
            "tags_all": ["all"],
            "tags_any": ["any", 2],
            "tags_none": ["none"],
            "created_from": "2026-01-01",
            "created_to": "2026-01-02",
            "added_from": "2026-01-03",
            "added_to": "2026-01-04",
            "owner": "o",
            "inbox": False,
            "custom_field_query": ["f", "exact", 1],
            "has_duplicates": True,
        }
    )
    assert filters.model_fields_set == set(SelectionFilters.model_fields)

    names, query = filters.parts("-title")

    assert names == NameFilters(
        correspondent="c",
        document_type="d",
        storage_path="s",
        tags_all=("all",),
        tags_any=("any", 2),
        tags_none=("none",),
        owner="o",
    )
    assert query == DocumentQuery(
        query="acme",
        more_like=4,
        title_contains="t",
        created_from=date(2026, 1, 1),
        created_to=date(2026, 1, 2),
        added_from=date(2026, 1, 3),
        added_to=date(2026, 1, 4),
        inbox=False,
        correspondent_isnull=False,
        document_type_isnull=True,
        storage_path_isnull=False,
        tagged=False,
        has_duplicates=True,
        custom_field_query=["f", "exact", 1],
        ordering="-title",
    )


@pytest.mark.parametrize("has", [True, False])
def test_a_has_filter_is_the_opposite_of_being_empty(has: bool) -> None:  # noqa: FBT001 -- a parametrised flag, not a switch
    _, query = SelectionFilters(
        has_correspondent=has, has_document_type=has, has_storage_path=has, has_tags=has
    ).parts()

    flags = (query.correspondent_isnull, query.document_type_isnull, query.storage_path_isnull)
    assert (flags, query.tagged) == ((not has,) * 3, has)


def test_filters_that_are_not_given_are_none() -> None:
    assert SelectionFilters().parts() == (NameFilters(), DocumentQuery())


@pytest.mark.parametrize(
    "filters",
    [
        {"correspondent": True},
        {"document_type": False},
        {"storage_path": True},
        {"owner": True},
        {"more_like_id": True},
        {"tags_all": [True]},
        {"tags_any": [1, False]},
        {"tags_none": [True]},
        {"created_from": 0},
        {"created_to": "2026-01-01T00:00:00"},
        {"added_from": datetime(2026, 1, 1, tzinfo=UTC)},
        {"added_to": 1767225600.0},
        {"created_from": "20260101"},
    ],
)
def test_a_flag_is_no_id_and_only_a_calendar_date_is_a_date(filters: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _ = SelectionFilters.model_validate(filters)


def test_a_row_holds_only_what_was_asked_for_and_has_a_value() -> None:
    row = ReadRow(id=1, web_url="u", tags=(), correspondent=None)

    assert row.model_dump() == {"id": 1, "web_url": "u", "tags": ()}


def test_a_row_can_hold_every_field_that_can_be_asked_for() -> None:
    names = cast("list[str]", TypeAdapter[ReadField](ReadField).json_schema()["enum"])

    assert set(names) - {"content"} == set(DocumentRow.model_fields) - {"id"}
    extra = {"id", "web_url", "content_total", "content_next", "too_large"}
    assert set(names) == set(ReadRow.model_fields) - extra


def test_a_row_is_valid_against_the_schema_without_its_absent_fields() -> None:
    schema = DocumentsRead.model_json_schema(mode="serialization")

    assert schema["$defs"]["ReadRow"]["required"] == ["id", "web_url"]
