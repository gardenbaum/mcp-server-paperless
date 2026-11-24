"""The filters a selection takes: strict, and the same as a search."""

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


def test_the_filters_become_names_and_a_query() -> None:
    filters = SelectionFilters.model_validate(
        {
            "query": "acme",
            "correspondent": "Acme",
            "has_document_type": False,
            "has_tags": True,
            "tags_any": ["a", 2],
            "created_from": "2026-01-02",
            "more_like_id": 4,
        }
    )

    names, query = filters.parts("-title")

    assert names == NameFilters(correspondent="Acme", tags_any=("a", 2))
    assert query == DocumentQuery.model_validate(
        {
            "query": "acme",
            "more_like": 4,
            "document_type_isnull": True,
            "tagged": True,
            "created_from": "2026-01-02",
            "ordering": "-title",
        }
    )


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
