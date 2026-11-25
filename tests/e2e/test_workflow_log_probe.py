"""Probe P6 of PLMCP-87 (temporary, fails on purpose)."""

import json
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, drop_orphaned_workflow_parts

from e2e.helpers import consume_text, obj, parse, results

if TYPE_CHECKING:
    from e2e.helpers import Json, Stack

pytestmark = [pytest.mark.e2e]


def test_p6(stack: Stack) -> None:
    seen: Json = {}
    prefix = f"contract-workflow-e2e-{uuid.uuid4().hex[:6]}"
    made: list[object] = []
    tag = obj(parse(stack.admin.post("/api/tags/", json={"name": prefix})))["id"]

    def flow(name: str, trigger: Json, actions: list[Json]) -> None:
        body = {"name": f"{prefix}-{name}", "triggers": [trigger], "actions": actions}
        made.append(obj(parse(stack.admin.post("/api/workflows/", json=body)))["id"])

    title: Json = {"type": 1, "assign_tags": [tag]}
    flow("cons-no", {"type": 1, "filter_filename": "never-*"}, [title])
    flow("cons-yes", {"type": 1, "filter_filename": f"{prefix}*"}, [title])
    flow("added-no", {"type": 2, "filter_has_tags": [stack.seeded.inbox_tag]}, [title])
    flow("added-yes", {"type": 2, "match": "words", "matching_algorithm": 1}, [title])
    flow("updated-no", {"type": 3, "filter_filename": "never"}, [title])
    flow("manual", {"type": 5}, [title])
    document = consume_text(stack.admin, f"{prefix}-words here")
    path = f"/api/documents/{document}/"
    row = obj(parse(stack.admin.get(path)))
    seen["document"] = {
        k: row[k] for k in ("added", "created", "modified", "original_file_name", "tags")
    }
    _ = stack.admin.patch(path, json={"title": f"{prefix} changed"})
    tasks = results(
        stack.admin.get(
            "/api/tasks/",
            params={"task_type": "consume_file", "page_size": 3, "ordering": "-date_created"},
        )
    )
    seen["tasks"] = tasks[:2]
    window = {
        "date_created_after": "2020-01-01T00:00:00Z",
        "date_created_before": str(row["added"]),
        "page_size": 2,
    }
    answer = stack.admin.get("/api/tasks/", params={"task_type": "consume_file", **window})
    seen["window"] = [answer.status_code, [t["task_id"] for t in results(answer)]]
    seen["history"] = parse(stack.admin.get(f"{path}history/"))
    query = json.dumps(["e2e-string", "exists", True])
    found = stack.admin.get(
        "/api/documents/", params={"id__in": str(document), "custom_field_query": query}
    )
    seen["query"] = [found.status_code, obj(parse(found)).get("count")]
    bad = stack.admin.get(
        "/api/documents/",
        params={"id__in": str(document), "custom_field_query": '["nope","exists",true]'},
    )
    seen["bad query"] = [bad.status_code, bad.text[:200]]
    lines = cast("list[str]", parse(stack.admin.get("/api/logs/paperless/", params={"limit": 400})))
    seen["log"] = [
        x for x in lines if "orkflow" in x or "onsum" in x or "match" in x or prefix in x
    ][-60:]
    for number in made:
        _ = stack.admin.delete(f"/api/workflows/{number}/")
    drop_orphaned_workflow_parts(stack)
    _ = stack.admin.delete(f"/api/tags/{tag}/")
    delete_and_empty_trash(stack, [document])
    pytest.fail(json.dumps(seen, indent=1, default=str), pytrace=False)
