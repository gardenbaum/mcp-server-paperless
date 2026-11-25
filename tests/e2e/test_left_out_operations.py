"""Why some operations and fields of the Paperless API have no tool: what 3.5.1 does with them.

The owner approved leaving them out (decision-99). They are no ports, so only the live stack can
show it.
"""

import time
import uuid
from typing import TYPE_CHECKING

import pytest

from e2e.helpers import consume_text, num, obj, parse, seq

if TYPE_CHECKING:
    from collections.abc import Iterator

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.decision("decision-99")]

BUILD_SECONDS = 60.0


@pytest.fixture
def document(stack: Stack) -> Iterator[int]:
    """A new document of the admin (deleted again, also when the test fails)."""
    created = consume_text(stack.admin, f"plmcp83-{uuid.uuid4().hex}")
    yield created
    _ = stack.admin.delete(f"/api/documents/{created}/")


def test_json_to_the_email_of_one_document_is_a_server_error(stack: Stack, document: int) -> None:
    body = {"addresses": "nobody@example.invalid", "subject": "x", "message": "x"}

    reply = stack.admin.post(f"/api/documents/{document}/email/", json=body)

    assert reply.status_code == 500


def test_the_rebuild_of_a_bundle_does_not_read_its_body(stack: Stack, document: int) -> None:
    made = {"document_ids": [document], "expiration_days": 3, "file_version": "original"}
    other = {
        "document_ids": [stack.seeded.public_document],
        "expiration_days": 30,
        "file_version": "archive",
    }
    created = stack.admin.post("/api/share_link_bundles/", json=made).raise_for_status()
    before = obj(parse(created))
    path = f"/api/share_link_bundles/{num(before['id'])}/"
    try:
        # A bundle that is being built refuses a rebuild.
        deadline = time.monotonic() + BUILD_SECONDS
        while obj(parse(stack.admin.get(path)))["status"] not in {"ready", "failed"}:
            assert time.monotonic() < deadline, "the bundle was not built in time"
            time.sleep(0.5)
        reply = stack.admin.post(f"{path}rebuild/", json=other)
        after = obj(parse(reply.raise_for_status()))
    finally:
        _ = stack.admin.delete(path)

    kept = ("documents", "expiration", "file_version")
    assert {name: after[name] for name in kept} == {name: before[name] for name in kept}


def test_the_values_of_a_custom_field_vanish_when_its_data_type_changes(
    stack: Stack, document: int
) -> None:
    made = {"name": f"plmcp83-{uuid.uuid4().hex}", "data_type": "string"}
    field = num(obj(parse(stack.admin.post("/api/custom_fields/", json=made)))["id"])
    path, values = f"/api/custom_fields/{field}/", [{"field": field, "value": "42"}]
    mine = f"/api/documents/{document}/"
    try:
        _ = stack.admin.patch(mine, json={"custom_fields": values}).raise_for_status()
        _ = stack.admin.patch(path, json={"data_type": "integer"}).raise_for_status()
        changed = seq(obj(parse(stack.admin.get(mine)))["custom_fields"])
        _ = stack.admin.patch(path, json={"data_type": "string"}).raise_for_status()
        back = seq(obj(parse(stack.admin.get(mine)))["custom_fields"])
    finally:
        _ = stack.admin.delete(path)

    assert (changed, back) == ([{"field": field, "value": None}], values)
