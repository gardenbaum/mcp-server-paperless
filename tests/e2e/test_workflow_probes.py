"""Probes P1 to P5 of PLMCP-87 against a live Paperless (temporary: P2 to P5 fail on purpose).

P1 decides whether one part of a workflow can be written through its workflow: a PATCH that names
the other parts by their ids only has to leave them as they are.
"""

import json
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import SCRATCH_TAG, SCRATCH_WORKFLOW, live_backend

from e2e.helpers import obj, parse, seq

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx2
    from contract.conftest import Backend

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


@pytest.fixture
async def backend(stack: Stack) -> AsyncIterator[Backend]:
    async with live_backend(stack) as live:
        yield live


def body(response: httpx2.Response) -> Json:
    assert response.is_success, (response.status_code, response.text)
    return obj(parse(response))


def told(response: httpx2.Response) -> object:
    return [response.status_code, response.text[:600]]


def parts(row: Json, name: str) -> list[Json]:
    return [obj(item) for item in seq(row[name])]


def ids(row: Json, name: str) -> list[Json]:
    return [{"id": part["id"]} for part in parts(row, name)]


def rows(stack: Stack, path: str) -> list[Json]:
    found: list[Json] = []
    page: str | None = path
    number = 1
    while page:
        answer = body(stack.admin.get(path, params={"page_size": 100, "page": number}))
        found += [obj(item) for item in seq(answer["results"])]
        page = cast("str | None", answer["next"])
        number += 1
    return found


def by_id(stack: Stack, path: str) -> dict[object, Json]:
    return {row["id"]: row for row in rows(stack, path)}


def tag(stack: Stack) -> int:
    made = body(stack.admin.post("/api/tags/", json={"name": f"{SCRATCH_TAG}{uuid.uuid4().hex}"}))
    return cast("int", made["id"])


TRIGGER: Json = {"type": 1, "sources": [2], "filter_filename": "contract-workflow-never-*.none"}


def new(stack: Stack, **given: object) -> Json:
    made = {
        "name": f"{SCRATCH_WORKFLOW}{uuid.uuid4().hex}",
        "order": 9,
        "enabled": False,
        "triggers": [TRIGGER],
        "actions": [],
        **given,
    }
    return body(stack.admin.post("/api/workflows/", json=made))


def everything(stack: Stack) -> Json:
    """A disabled workflow with every trigger type and all 8 action types, none at its default."""
    one, two = tag(stack), tag(stack)
    seeded = stack.seeded
    field = seeded.custom_fields["string"]
    query = json.dumps([field, "exists", True])
    me = cast("int", obj(body(stack.admin.get("/api/ui_settings/"))["user"])["id"])
    triggers: list[Json] = [
        {**TRIGGER, "filter_path": "/never/*"},
        {
            "type": 2,
            "match": "never",
            "matching_algorithm": 3,
            "is_insensitive": False,
            "filter_has_tags": [one],
            "filter_has_all_tags": [two],
            "filter_has_not_tags": [seeded.inbox_tag],
            "filter_has_any_correspondents": [seeded.correspondent],
            "filter_has_not_document_types": [seeded.document_type],
            "filter_has_storage_path": seeded.storage_path,
            "filter_custom_field_query": query,
        },
        {"type": 3, "filter_has_tags": [two]},
        {
            "type": 4,
            "schedule_date_field": "custom_field",
            "schedule_date_custom_field": seeded.custom_fields["date"],
            "schedule_offset_days": -3,
            "schedule_is_recurring": True,
            "schedule_recurring_interval_days": 7,
            "filter_has_tags": [one],
        },
        {"type": 5, "filter_filename": "*.never"},
    ]
    actions: list[Json] = [
        {
            "type": 1,
            "assign_title": "{{ doc_id }}",
            "assign_tags": [one, two],
            "assign_correspondent": seeded.correspondent,
            "assign_owner": me,
            "assign_view_users": [me],
            "assign_custom_fields": [field],
            "assign_custom_fields_values": {str(field): "x"},
        },
        {"type": 2, "remove_tags": [one], "remove_all_owners": True, "remove_change_users": [me]},
        {
            "type": 3,
            "email": {"subject": "s", "body": "b", "to": "a@e.invalid", "include_document": True},
        },
        {
            "type": 4,
            "webhook": {
                "url": "https://u:p@e.invalid/x?y=z",
                "use_params": False,
                "as_json": True,
                "body": '{"a": 1}',
                "headers": {"X-Token": "planted"},
                "include_document": True,
            },
        },
        {"type": 5, "passwords": ["planted-one", "planted-two"]},
        {"type": 6},
        {"type": 7},
        {
            "type": 8,
            "ai_suggestion_fields": ["title", "tags"],
            "ai_create_missing": True,
            "ai_overwrite_existing": True,
        },
    ]
    return new(stack, triggers=triggers, actions=actions)


def patch(stack: Stack, row: Json, **given: object) -> Json:
    return body(stack.admin.patch(f"/api/workflows/{row['id']}/", json=given))


def read(stack: Stack, row: Json) -> Json:
    return body(stack.admin.get(f"/api/workflows/{row['id']}/"))


@pytest.mark.usefixtures("backend")
async def test_p1_actions_named_by_id_only_keep_everything(stack: Stack) -> None:
    made = everything(stack)
    assert read(stack, made) == made
    assert patch(stack, made, actions=ids(made, "actions")) == made
    assert read(stack, made) == made


@pytest.mark.usefixtures("backend")
async def test_p1_triggers_named_by_id_only_keep_everything(stack: Stack) -> None:
    made = new(stack, triggers=parts_without_ids(everything(stack), "triggers"))
    assert patch(stack, made, triggers=ids(made, "triggers")) == made
    assert read(stack, made) == made


@pytest.mark.usefixtures("backend")
async def test_p1_triggers_sent_in_full_keep_everything(stack: Stack) -> None:
    made = everything(stack)
    assert patch(stack, made, triggers=made["triggers"]) == made
    assert read(stack, made) == made


def parts_without_ids(row: Json, name: str) -> list[Json]:
    return [{k: v for k, v in part.items() if k != "id"} for part in parts(row, name)]


@pytest.mark.usefixtures("backend")
async def test_p1_one_changed_action_changes_nothing_else(stack: Stack) -> None:
    made = everything(stack)
    for place, change in (
        (0, {"assign_title": "changed"}),
        (0, {"assign_tags": []}),
        (1, {"remove_all_owners": False}),
        (2, {"email": {"subject": "t", "body": "c", "to": "b@e.invalid"}}),
        (3, {"webhook": {"url": "https://u:p@e.invalid/z", "headers": {"X-Token": "other"}}}),
    ):
        before = parts(read(stack, made), "actions")
        sent = [dict(item) for item in ids(made, "actions")]
        sent[place] |= change
        after = parts(patch(stack, made, actions=sent), "actions")
        assert [a["id"] for a in after] == [a["id"] for a in before]
        assert after[:place] + after[place + 1 :] == before[:place] + before[place + 1 :]
        name = next(iter(change))
        rest = {k: v for k, v in after[place].items() if k != name}
        assert rest == {k: v for k, v in before[place].items() if k != name}
        if isinstance(change[name], dict):
            assert obj(after[place][name]).items() >= cast("Json", change[name]).items()
        else:
            assert after[place][name] == change[name]


@pytest.mark.usefixtures("backend")
async def test_p1_one_changed_trigger_changes_nothing_else(stack: Stack) -> None:
    made = everything(stack)
    sent = [dict(part) for part in parts(made, "triggers")]
    sent[1]["filter_has_tags"] = []
    sent[1]["match"] = "other"
    after = patch(stack, made, triggers=sent)
    assert parts(after, "triggers") == sent
    assert after["actions"] == made["actions"]


@pytest.mark.usefixtures("backend")
async def test_p1_add_move_and_remove_keep_the_others(stack: Stack) -> None:
    made = everything(stack)
    kept = {a["id"]: a for a in parts(made, "actions")}
    wanted = [item["id"] for item in ids(made, "actions")][::-1]

    moved = patch(stack, made, actions=[{"id": n} for n in wanted])
    assert parts(moved, "actions") == [kept[n] for n in wanted]
    assert parts(read(stack, made), "actions") == [kept[n] for n in wanted]

    fresh: Json = {"type": 1, "assign_title": "new"}
    added = parts(patch(stack, made, actions=[{"id": n} for n in wanted] + [fresh]), "actions")
    assert added[:-1] == [kept[n] for n in wanted]
    assert added[-1]["assign_title"] == "new"
    assert added[-1]["id"] not in kept

    gone = wanted.pop(3)
    less = patch(stack, made, actions=[{"id": n} for n in wanted])
    assert parts(less, "actions") == [kept[n] for n in wanted]
    assert gone not in by_id(stack, "/api/workflow_actions/")

    triggers = {t["id"]: t for t in parts(made, "triggers")}
    more = parts(patch(stack, made, triggers=[*triggers.values(), {"type": 2}]), "triggers")
    assert {n: t for t in more if (n := t["id"]) in triggers} == triggers
    assert len(more) == len(triggers) + 1
    fewer = parts(patch(stack, made, triggers=list(triggers.values())), "triggers")
    assert fewer == list(triggers.values())


def probe(name: str, seen: Json) -> None:
    pytest.fail(f"{name} {json.dumps(seen, indent=1, default=str)}", pytrace=False)


@pytest.mark.usefixtures("backend")
async def test_p2_the_single_endpoints(stack: Stack, backend: Backend) -> None:
    seen: Json = {}
    made = everything(stack)
    actions = parts(made, "actions")
    triggers = parts(made, "triggers")
    for place, what in ((2, "email"), (3, "webhook")):
        path = f"/api/workflow_actions/{actions[place]['id']}/"
        row = actions[place]
        seen[f"patch {what}"] = told(stack.admin.patch(path, json={what: row[what]}))
        seen[f"put {what}"] = told(stack.admin.put(path, json=row))
        seen[f"patch title of {what}"] = told(stack.admin.patch(path, json={"assign_title": "x"}))
        fresh = {k: v for k, v in row.items() if k != "id"}
        seen[f"post {what}"] = told(stack.admin.post("/api/workflow_actions/", json=fresh))
    path = f"/api/workflow_triggers/{triggers[0]['id']}/"
    seen["patch consumption sources"] = told(stack.admin.patch(path, json={"sources": [1]}))
    seen["get trigger equals nested"] = body(stack.admin.get(path)) == triggers[0]
    path = f"/api/workflow_actions/{actions[3]['id']}/"
    seen["get action equals nested"] = body(stack.admin.get(path)) == actions[3]
    seen["missing"] = [
        told(stack.admin.get("/api/workflow_actions/999999/")),
        told(stack.admin.get("/api/workflow_triggers/999999/")),
    ]
    path = f"/api/workflow_actions/{actions[6]['id']}/"
    seen["delete held"] = told(stack.admin.delete(path))
    seen["left"] = [a["type"] for a in parts(read(stack, made), "actions")]
    for rights in (
        {"view_workflow"},
        {"view_workflowtrigger"},
        {"view_workflowaction"},
        {"view_workflow", "view_workflowtrigger", "view_workflowaction"},
        {"view_workflow", "change_workflow"},
    ):
        user = backend.restricted(frozenset(rights))
        answers: Json = {}
        for path in (
            f"/api/workflows/{made['id']}/",
            f"/api/workflow_triggers/{triggers[0]['id']}/",
            f"/api/workflow_actions/{actions[3]['id']}/",
            "/api/workflow_triggers/999999/",
        ):
            try:
                got = await user.raw.get(path, {}, max_bytes=100_000)
                answers[path] = str(got)[:80]
            except Exception as error:  # noqa: BLE001 -- a probe records whatever happens
                answers[path] = f"{type(error).__name__}: {error}"
        seen[f"rights {sorted(rights)}"] = answers
    probe("P2", seen)


@pytest.mark.usefixtures("backend")
async def test_p3_prune(stack: Stack) -> None:
    seen: Json = {}
    orphan = body(stack.admin.post("/api/workflow_triggers/", json={"type": 2}))["id"]
    loose = body(stack.admin.post("/api/workflow_actions/", json={"type": 6}))["id"]
    other = new(stack, actions=[{"type": 6}])
    gone = new(stack, actions=[{"type": 6}])

    def state() -> Json:
        triggers = by_id(stack, "/api/workflow_triggers/")
        actions = by_id(stack, "/api/workflow_actions/")
        return {
            "orphan trigger": orphan in triggers,
            "orphan action": loose in actions,
            "trigger of deleted": parts(gone, "triggers")[0]["id"] in triggers,
            "action of deleted": parts(gone, "actions")[0]["id"] in actions,
        }

    seen["after posts"] = state()
    _ = new(stack)
    seen["after a create"] = state()
    seen["delete"] = told(stack.admin.delete(f"/api/workflows/{gone['id']}/"))
    seen["after a delete"] = state()
    _ = patch(stack, other, order=3)
    seen["after another update"] = state()
    probe("P3", seen)


@pytest.mark.usefixtures("backend")
async def test_p4_foreign_and_vanished_ids(stack: Stack) -> None:
    seen: Json = {}
    made = new(stack, triggers=[TRIGGER, {"type": 2, "match": "n", "matching_algorithm": 3}])
    made = patch(stack, made, actions=[{"type": 6}, {"type": 1, "assign_title": "a"}])
    _, second = parts(made, "triggers")
    _, title = parts(made, "actions")
    _ = patch(stack, made, triggers=[parts(made, "triggers")[0]], actions=ids(made, "actions")[:1])
    path = f"/api/workflows/{made['id']}/"
    vanished = [{"id": parts(made, "triggers")[0]["id"]}, {"id": second["id"]}]
    seen["vanished trigger id-only"] = told(stack.admin.patch(path, json={"triggers": vanished}))
    seen["state 1"] = read(stack, made)["triggers"]
    answer = stack.admin.patch(path, json={"triggers": [parts(made, "triggers")[0], second]})
    seen["vanished trigger full"] = told(answer)
    seen["vanished action id-only"] = told(
        stack.admin.patch(path, json={"actions": [*ids(made, "actions")[:1], {"id": title["id"]}]})
    )
    seen["state 2"] = read(stack, made)["actions"]
    victim = new(stack, actions=[{"type": 1, "assign_title": "victim"}])
    stolen = {**parts(victim, "actions")[0], "assign_title": "thief"}
    seen["foreign action"] = told(stack.admin.patch(path, json={"actions": [stolen]}))
    seen["victim after"] = parts(read(stack, victim), "actions")[0]["assign_title"]
    taken = {**parts(victim, "triggers")[0], "filter_filename": "thief-*.none"}
    copied = new(stack, triggers=[taken], actions=[stolen])
    seen["post trigger id"] = [ids(copied, "triggers"), ids(victim, "triggers")]
    seen["post action id"] = [ids(copied, "actions"), ids(victim, "actions")]
    seen["victim trigger"] = parts(read(stack, victim), "triggers")[0]["filter_filename"]
    for data in (
        {"actions": [{"id": parts(victim, "actions")[0]["id"], "type": 3}]},
        {"actions": [{"type": 7}], "triggers": ids(read(stack, victim), "triggers")},
        {"triggers": [{"id": parts(read(stack, victim), "triggers")[0]["id"], "type": 2}]},
        {"triggers": [], "actions": []},
    ):
        key = f"rules {json.dumps(data)}"
        seen[key] = told(stack.admin.patch(f"/api/workflows/{victim['id']}/", json=data))
    probe("P4", seen)


@pytest.mark.usefixtures("backend")
async def test_p5_types_five_and_eight_and_the_list(stack: Stack) -> None:
    seen: Json = {}
    one = tag(stack)
    manual: Json = {
        "type": 5,
        "sources": [1],
        "filter_path": "/x/*",
        "filter_filename": "*.y",
        "filter_has_tags": [one],
        "schedule_offset_days": 4,
        "match": "m",
        "matching_algorithm": 1,
    }
    ai: Json = {"type": 8, "ai_suggestion_fields": ["title"]}
    path = "/api/workflows/"

    def post(**given: object) -> object:
        name = f"{SCRATCH_WORKFLOW}{uuid.uuid4().hex}"
        return told(stack.admin.post(path, json={"name": name, "enabled": False, **given}))

    made = new(stack, triggers=[manual, {"type": 5}])
    seen["manual stored"] = made["triggers"]
    seen["ai"] = post(triggers=[{"type": 2}], actions=[ai])
    seen["ai enabled"] = post(triggers=[{"type": 5}], actions=[ai], enabled=True)
    seen["ai consumption"] = post(triggers=[TRIGGER], actions=[ai])
    seen["ai bad field"] = post(
        triggers=[{"type": 2}], actions=[{"type": 8, "ai_suggestion_fields": ["x"]}]
    )
    seen["ocr without consumption"] = post(triggers=[{"type": 2}], actions=[{"type": 7}])
    for params in (
        {"trigger_type": 5},
        {"trigger_type": 99},
        {"enabled": "false"},
        {"enabled": "true"},
        {"enabled": "false", "trigger_type": 5},
        {"trigger_type": "x"},
    ):
        answer = stack.admin.get(path, params={"page_size": 100, **params})
        found: object = told(answer)
        if answer.is_success:
            mine = [r["id"] for r in seq_rows(answer) if r["id"] == made["id"]]
            found = {"count": body(answer)["count"], "mine": mine}
        seen[f"list {params}"] = found
    probe("P5", seen)


def seq_rows(answer: httpx2.Response) -> list[Json]:
    return [obj(item) for item in seq(body(answer)["results"])]
