"""How a live Paperless takes the parts of a workflow that a PATCH names by their ids (decision-81).

Paperless does not document it: a PATCH that names triggers and actions by their ids leaves them as
they are, hidden values included, and changes only what it sends of them. Writing single triggers
and actions rests on it (PLMCP-87), so this module says at once when a release changes it. The
workflows are disabled.
"""

import json
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import SCRATCH_TAG, SCRATCH_WORKFLOW, every_row, live_backend

from e2e.helpers import obj, parse, seq

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx2
    from contract.conftest import Backend

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-81")]

type Json = dict[str, object]


@pytest.fixture
async def backend(stack: Stack) -> AsyncIterator[Backend]:
    async with live_backend(stack) as live:
        yield live


def body(response: httpx2.Response) -> Json:
    assert response.is_success, (response.status_code, response.text)
    return obj(parse(response))


def parts(row: Json, name: str) -> list[Json]:
    return [obj(item) for item in seq(row[name])]


def ids(row: Json, name: str) -> list[Json]:
    return [{"id": part["id"]} for part in parts(row, name)]


def parts_without_ids(row: Json, name: str) -> list[Json]:
    return [{k: v for k, v in part.items() if k != "id"} for part in parts(row, name)]


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
    """A disabled workflow with every trigger type and all 8 action types, none at its default.

    What was sent is stored, so a test that finds it unchanged has found it. Type 8 (AI suggestions)
    is written by this server from PLMCP-87.3 on; here it is one whose settings have to stay.
    """
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
    made = new(stack, triggers=triggers, actions=actions)
    plain = new(stack, triggers=[{**TRIGGER, "type": 2}], actions=[{"type": 6}])
    for name, given in (("triggers", triggers), ("actions", actions)):
        default = parts(plain, name)[0]
        for sent, stored in zip(given, parts(made, name), strict=True):
            assert_stored(sent, stored)
            for setting in sent.keys() - {"type", "sources", "filter_filename"}:
                assert stored[setting] != default[setting], setting
    return made


def assert_stored(sent: Json, stored: Json) -> None:
    """Every setting in `sent` is stored as it was sent (the order of ids aside)."""
    for setting, value in sent.items():
        there = stored[setting]
        if setting in {"email", "webhook"}:
            assert_stored(obj(value), obj(there))
        elif isinstance(value, list) and all(isinstance(v, int) for v in seq(there)):
            assert sorted(cast("list[int]", value)) == sorted(cast("list[int]", there)), setting
        else:
            assert there == value, setting


def patch(stack: Stack, row: Json, **given: object) -> Json:
    return body(stack.admin.patch(f"/api/workflows/{row['id']}/", json=given))


def read(stack: Stack, row: Json) -> Json:
    return body(stack.admin.get(f"/api/workflows/{row['id']}/"))


@pytest.mark.usefixtures("backend")
async def test_actions_named_by_id_only_keep_everything(stack: Stack) -> None:
    made = everything(stack)
    assert read(stack, made) == made
    assert patch(stack, made, actions=ids(made, "actions")) == made
    assert read(stack, made) == made


@pytest.mark.usefixtures("backend")
async def test_triggers_named_by_id_only_keep_everything(stack: Stack) -> None:
    made = new(stack, triggers=parts_without_ids(everything(stack), "triggers"))
    assert patch(stack, made, triggers=ids(made, "triggers")) == made
    assert read(stack, made) == made


@pytest.mark.usefixtures("backend")
async def test_triggers_sent_in_full_keep_everything(stack: Stack) -> None:
    made = everything(stack)
    assert patch(stack, made, triggers=made["triggers"]) == made
    assert read(stack, made) == made


@pytest.mark.usefixtures("backend")
async def test_one_changed_action_changes_nothing_else(stack: Stack) -> None:
    made = everything(stack)
    mail: Json = {"subject": "t", "body": "c", "to": "b@e.invalid"}
    hook: Json = {"url": "https://u:p@e.invalid/z", "headers": {"X-Token": "other"}}
    changes: list[tuple[int, str, object]] = [
        (0, "assign_title", "changed"),
        (0, "assign_tags", []),
        (1, "remove_all_owners", False),
        (2, "email", mail),
        (3, "webhook", hook),
    ]
    for place, name, value in changes:
        before = parts(read(stack, made), "actions")
        sent = [dict(item) for item in ids(made, "actions")]
        sent[place][name] = value
        after = parts(patch(stack, made, actions=sent), "actions")
        assert [a["id"] for a in after] == [a["id"] for a in before]
        assert after[:place] + after[place + 1 :] == before[:place] + before[place + 1 :]
        rest = {k: v for k, v in after[place].items() if k != name}
        assert rest == {k: v for k, v in before[place].items() if k != name}
        if isinstance(value, dict):
            assert obj(after[place][name]).items() >= cast("Json", value).items()
        else:
            assert after[place][name] == value


@pytest.mark.usefixtures("backend")
async def test_one_changed_trigger_changes_nothing_else(stack: Stack) -> None:
    made = everything(stack)
    sent = [dict(part) for part in parts(made, "triggers")]
    sent[1]["filter_has_tags"] = []
    sent[1]["match"] = "other"
    after = patch(stack, made, triggers=sent)
    assert parts(after, "triggers") == sent
    assert after["actions"] == made["actions"]


@pytest.mark.usefixtures("backend")
async def test_add_move_and_remove_keep_the_others(stack: Stack) -> None:
    made = everything(stack)
    kept = {a["id"]: a for a in parts(made, "actions")}
    wanted = [item["id"] for item in ids(made, "actions")][::-1]

    def actions() -> list[Json]:
        # Not the answer of the PATCH: it lists actions by id, whatever their order.
        return parts(read(stack, made), "actions")

    _ = patch(stack, made, actions=[{"id": n} for n in wanted])
    assert actions() == [kept[n] for n in wanted]

    fresh: Json = {"type": 1, "assign_title": "new"}
    _ = patch(stack, made, actions=[*({"id": n} for n in wanted), fresh])
    added = actions()
    assert added[:-1] == [kept[n] for n in wanted]
    assert added[-1]["assign_title"] == "new"
    assert added[-1]["id"] not in kept

    gone = wanted.pop(3)
    _ = patch(stack, made, actions=[{"id": n} for n in wanted])
    assert actions() == [kept[n] for n in wanted]
    assert gone not in [row["id"] for row in every_row(stack, "/api/workflow_actions/")]

    triggers = {t["id"]: t for t in parts(made, "triggers")}
    more = parts(patch(stack, made, triggers=[*triggers.values(), {"type": 2}]), "triggers")
    assert {n: t for t in more if (n := t["id"]) in triggers} == triggers
    assert len(more) == len(triggers) + 1
    fewer = parts(patch(stack, made, triggers=list(triggers.values())), "triggers")
    assert fewer == list(triggers.values())
