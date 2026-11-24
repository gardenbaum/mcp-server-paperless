"""What one call may say about many documents: the same fields as a single update, strictly."""

from datetime import date
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from mcp_server_paperless.domain.bulk_items import DocumentNote, DocumentValues
from mcp_server_paperless.domain.documents import DocumentEdit

if TYPE_CHECKING:
    from pydantic import JsonValue

pytestmark = pytest.mark.decision("decision-73")


def test_the_fields_are_those_of_a_single_update() -> None:
    of_the_call = {"document_id"}
    single = DocumentEdit.model_fields.keys() - {"create_missing_tags", "confirm"}

    assert DocumentValues.model_fields.keys() - of_the_call == single


def test_every_field_reaches_the_edit_and_what_is_left_out_stays_out() -> None:
    given: dict[str, JsonValue] = {
        "title": "t",
        "correspondent": "c",
        "document_type": 2,
        "storage_path": "s",
        "created": "2026-01-02",
        "archive_serial_number": 0,
        "owner": "o",
        "clear": ["owner"],
        "tags_add": ["a"],
        "tags_remove": ["b"],
        "tags_set": [],
        "custom_fields": {"f": None},
        "remove_custom_fields": ["g"],
    }

    edit = DocumentValues.model_validate({"document_id": 5, **given}).edit(create_missing_tags=True)
    empty = DocumentValues(document_id=5).edit(create_missing_tags=False)

    assert given.keys() == DocumentValues.model_fields.keys() - {"document_id"}
    assert edit == DocumentEdit.model_validate({**given, "create_missing_tags": True})
    assert edit.created == date(2026, 1, 2)
    assert (edit.tags_set, edit.custom_fields) == ((), {"f": None})
    assert empty == DocumentEdit()


@pytest.mark.parametrize(
    "given",
    [
        {"document_id": True},
        {"document_id": 1, "titel": "a typing mistake"},
        {"document_id": 1, "confirm": True},
        {"document_id": 1, "archive_serial_number": -1},
        {"document_id": 1, "archive_serial_number": True},
        {"document_id": 1, "correspondent": True},
        {"document_id": 1, "owner": False},
        {"document_id": 1, "tags_add": [True]},
        {"document_id": 1, "created": 0},
        {"document_id": 1, "clear": ["title"]},
        {},
    ],
)
def test_what_could_be_meant_otherwise_is_refused(given: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        _ = DocumentValues.model_validate(given)


@pytest.mark.parametrize(
    "given", [{"document_id": True, "note": "n"}, {"document_id": 1}, {"document_id": 1, "n": "x"}]
)
def test_a_note_names_its_document_and_its_text(given: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        _ = DocumentNote.model_validate(given)
