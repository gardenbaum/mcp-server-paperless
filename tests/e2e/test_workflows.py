"""The workflows toolset through the assembled server against the seeded Paperless stack.

Every workflow here is disabled or has a trigger that no file matches (`NEVER`): a live workflow
acts on documents other tests consume.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, drop_orphaned_workflow_parts
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume_text, results
from e2e.test_bulk import make
from e2e.test_bulk_items import user
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack
    from e2e.test_bulk_items import Signed

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

__all__ = ["user"]  # the fixture

type Json = dict[str, object]
type Connected = Client[FastMCPTransport]

# Workflows this module creates; the contract suite's cleanup removes leftovers of a crashed run.
SCRATCH = "contract-workflow-e2e-"
NEVER = {"type": "consumption", "filename": "contract-workflow-never-*.none"}


@pytest.fixture
async def client(stack: Stack) -> AsyncIterator[Connected]:
    server = create_server(
        paperless_settings(stack.base_url, stack.admin_token),
        server_settings(toolsets=["core", "workflows"]),
    )
    async with Client(server) as connected:
        yield connected
    # Whatever a test left behind, also when it failed.
    for row in results(stack.admin.get("/api/workflows/", params={"page_size": 100})):
        if str(row["name"]).startswith(SCRATCH):
            _ = stack.admin.delete(f"/api/workflows/{row['id']}/")
    drop_orphaned_workflow_parts(stack)
    for row in results(stack.admin.get("/api/tags/", params={"name__istartswith": SCRATCH})):
        _ = stack.admin.delete(f"/api/tags/{row['id']}/")


async def call(client: Connected, tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(client: Connected, tool: str, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def stored(stack: Stack, name: str) -> list[Json]:
    """The workflows of that name as Paperless itself lists them."""
    found = results(stack.admin.get("/api/workflows/", params={"name__iexact": name}))
    return [row for row in found if row["name"] == name]


async def test_a_workflow_is_filed_by_names_read_changed_and_deleted(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    created = await call(
        client,
        "create_workflow",
        name=name,
        enabled=False,
        triggers=[
            NEVER,
            {
                "type": "added",
                "has_tags": ["e2e-finance"],
                "has_any_correspondents": ["e2e-acme"],
                "has_not_document_types": ["e2e-invoice"],
            },
        ],
        actions=[
            {
                "type": "assignment",
                "assign_title": "{{ correspondent }} {{ created_year }}",
                "assign_tags": ["e2e-inbox"],
                "assign_storage_path": "e2e-archive",
                "assign_custom_fields_values": {"e2e-string": "filed"},
            }
        ],
    )

    [raw] = stored(stack, name)
    tags = {str(t["name"]): t["id"] for t in results(stack.admin.get("/api/tags/"))}
    [consumed, added] = cast("list[Json]", raw["triggers"])
    [assigned] = cast("list[Json]", raw["actions"])
    assert added["filter_has_tags"] == [tags["e2e-finance"]]
    assert assigned["assign_tags"] == [tags["e2e-inbox"]]
    assert consumed["filter_filename"] == NEVER["filename"]
    [shown_trigger, shown_added] = cast("list[Json]", created["triggers"])
    assert shown_trigger["settings"] == {"filename": NEVER["filename"]}
    assert shown_added["settings"] == {
        "has_tags": ["e2e-finance"],
        "has_any_correspondents": ["e2e-acme"],
        "has_not_document_types": ["e2e-invoice"],
    }
    [shown_action] = cast("list[Json]", created["actions"])
    settings = cast("Json", shown_action["settings"])
    assert settings["assign_tags"] == ["e2e-inbox"]
    assert settings["assign_storage_path"] == "e2e-archive"
    assert settings["assign_custom_fields"] == ["e2e-string"]
    assert settings["assign_custom_fields_values"] == {"e2e-string": "filed"}

    listed = {
        str(w["name"])
        for w in cast("list[Json]", (await call(client, "list_workflows"))["workflows"])
    }
    assert name in listed
    read = await call(client, "get_workflow", workflow=name.upper())
    assert read == {**{k: v for k, v in created.items() if k != "note"}, "part": None}

    renamed = await call(
        client, "update_workflow", workflow=name, name=f"{name}-b", triggers=[NEVER], order=9
    )
    assert (renamed["name"], renamed["order"]) == (f"{name}-b", 9)
    assert [t["type_name"] for t in cast("list[Json]", renamed["triggers"])] == [
        "consumption started"
    ]

    text = await refused(client, "delete_workflow", workflow=f"{name}-b")
    assert "Nothing was changed" in text
    assert "its 1 trigger and 1 action are lost" in text
    assert len(stored(stack, f"{name}-b")) == 1
    gone = await call(client, "delete_workflow", workflow=f"{name}-b", confirm=True)
    assert gone["name"] == f"{name}-b"
    assert stored(stack, f"{name}-b") == []


async def test_a_workflow_that_acts_is_refused_until_confirmed_and_secrets_stay_hidden(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    hook = {"url": "https://hooks.example.org/in?token=s3", "headers": {"X-Token": "real-token"}}
    acting: Json = {
        "name": name,
        "triggers": [NEVER],
        "actions": [
            {"type": "webhook", "webhook": hook},
            {"type": "email", "email": {"subject": "s", "body": "b", "to": "a@example.org"}},
        ],
    }

    text = await refused(client, "create_workflow", **acting)
    assert stored(stack, name) == []
    assert "send a POST request to 'hooks.example.org'" in text
    assert "send an e-mail to 'a@example.org'" in text
    assert "s3" not in text
    assert "real-token" not in text

    made = await call(client, "create_workflow", confirm=True, **acting)
    assert "real-token" not in str(made)
    [raw] = stored(stack, name)
    [webhook, _] = cast("list[Json]", raw["actions"])
    assert cast("Json", webhook["webhook"])["headers"] == {"X-Token": "real-token"}

    off = await call(client, "update_workflow", workflow=name, enabled=False)
    assert off["enabled"] is False
    assert "Confirming would change the workflow" in await refused(
        client, "update_workflow", workflow=name, enabled=True
    )
    [first, _] = cast("list[Json]", off["actions"])
    kept = await call(
        client,
        "update_workflow",
        workflow=name,
        actions=[
            {
                "type": "webhook",
                "id": first["id"],
                "webhook": {
                    "url": "https://hooks.example.org/other",
                    "headers": {"X-Token": "(hidden)"},
                },
            }
        ],
    )
    assert [a["type_name"] for a in cast("list[Json]", kept["actions"])] == ["webhook"]
    [raw] = stored(stack, name)
    [webhook] = cast("list[Json]", raw["actions"])
    assert cast("Json", webhook["webhook"])["headers"] == {"X-Token": "real-token"}
    assert cast("Json", webhook["webhook"])["url"] == "https://hooks.example.org/other"


async def test_an_unsupported_type_and_a_misplaced_setting_are_refused_with_an_error(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"

    passwords = await refused(
        client, "create_workflow", name=name, actions=[{"type": "password removal"}]
    )
    misplaced = await refused(
        client,
        "create_workflow",
        name=name,
        actions=[{"type": "move to trash", "assign_title": "x"}],
    )
    nothing = await refused(client, "create_workflow", name=name, triggers=[{"type": "hourly"}])

    assert "cannot be written by this server" in passwords
    assert "'assign_title' belongs to assignment actions" in misplaced
    assert "No workflow trigger type is called 'hourly'" in nothing
    assert stored(stack, name) == []


@pytest.mark.decision("decision-86")
async def test_manual_triggers_and_ai_suggestions_are_written(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    ai = {"type": "apply AI suggestions", "ai_suggestion_fields": ["title", "tags"]}
    given: Json = {
        "name": name,
        "triggers": [{"type": "manual", "has_tags": ["e2e-finance"], "filename": "never-*"}],
        "actions": [ai],
    }

    text = await refused(client, "create_workflow", **given)
    assert stored(stack, name) == []
    made = await call(client, "create_workflow", **given, confirm=True)
    [raw] = stored(stack, name)

    assert "runs only when a user runs it by hand (manual, filtered by" in text
    assert "ask the AI backend, at least once each, about the text of every document" in text
    [trigger] = cast("list[Json]", raw["triggers"])
    assert (trigger["type"], trigger["filter_filename"]) == (5, "never-*")
    [action] = cast("list[Json]", raw["actions"])
    assert action["ai_suggestion_fields"] == ["title", "tags"]
    [shown] = cast("list[Json]", made["actions"])
    assert shown["settings"] == {"ai_suggestion_fields": ["title", "tags"]}
    off = await call(client, "update_workflow", workflow=name, enabled=False)
    assert off["enabled"] is False
    part: Json = {"workflow": name, "operation": "change", "action_id": action["id"]}
    more = {"type": 8, "ai_create_missing": True}
    _ = await call(client, "update_workflow_part", **part, action=more)
    [raw] = stored(stack, name)
    [action] = cast("list[Json]", raw["actions"])
    assert (action["ai_suggestion_fields"], action["ai_create_missing"]) == (
        ["title", "tags"],
        True,
    )
    consumed = await refused(client, "update_workflow", workflow=name, triggers=[NEVER])
    assert "AI suggestions" in consumed


@pytest.mark.decision("decision-86")
async def test_a_manual_trigger_written_here_is_run(client: Connected, stack: Stack) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    title = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    _ = await call(
        client,
        "create_workflow",
        name=name,
        triggers=[{"type": "manual"}],
        actions=[{"type": "assignment", "assign_title": f"{title} ran"}],
    )
    document = consume_text(stack.admin, title)
    try:
        arguments: Json = {"workflow": name, "document_ids": [document], "confirm": True}
        done = await call(client, "run_workflow", **arguments)
        row = cast("Json", stack.admin.get(f"/api/documents/{document}/").json())
    finally:
        delete_and_empty_trash(stack, [document])

    assert done["started"] is True
    assert row["title"] == f"{title} ran"


@pytest.mark.decision("decision-81")
async def test_the_workflows_that_use_an_object_are_found_and_their_parts_read(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    tag = f"{SCRATCH}tag-{uuid.uuid4().hex[:8]}"
    number = make(stack, "tags", {"name": tag})
    hook = {"url": "https://user:planted@h.invalid/x", "headers": {"X-Token": "planted"}}
    _ = await call(
        client,
        "create_workflow",
        name=name,
        enabled=False,
        triggers=[
            NEVER,
            {"type": "added", "has_tags": [tag], "custom_field_query": '["e2e-url","exists",true]'},
        ],
        actions=[
            {"type": "webhook", "webhook": hook},
            {"type": "removal", "remove_tags": [tag]},
        ],
    )
    other = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    _ = await call(client, "create_workflow", name=other, triggers=[NEVER])

    async def mine(**given: object) -> dict[str, Json]:
        listed = cast("list[Json]", (await call(client, "list_workflows", **given))["workflows"])
        return {str(w["name"]): w for w in listed if w["name"] in {name, other}}

    by_tag = await mine(uses_kind="tag", uses=tag)
    assert list(by_tag) == [name]
    places = cast("list[Json]", by_tag[name]["used_in"])
    assert [(p["part"], p["setting"]) for p in places] == [
        ("trigger", "has_tags"),
        ("action", "remove_tags"),
    ]
    assert list(await mine(uses_kind="custom_field", uses="e2e-url")) == [name]
    assert list(await mine(uses_kind="tag", uses=number)) == [name]
    assert list(await mine(uses_kind="user", uses="admin")) == []
    assert list(await mine(enabled=True)) == [other]
    assert list(await mine(trigger_type="added")) == [name]
    assert set(await mine(trigger_type="consumption")) == {name, other}

    trigger = await call(client, "get_workflow", trigger_id=places[0]["id"])
    part = cast("Json", trigger["part"])
    assert cast("Json", cast("Json", part["trigger"])["settings"])["has_tags"] == [tag]
    assert [w["name"] for w in cast("list[Json]", part["workflows"])] == [name]
    [webhook, removal] = cast("list[Json]", by_tag[name]["actions"])
    shown = await call(client, "get_workflow", action_id=webhook["id"])
    assert cast("Json", shown["part"])["action"] == webhook
    assert "planted" not in str(shown)
    assert removal["id"] == places[1]["id"]
    assert "No workflow trigger 999999" in await refused(client, "get_workflow", trigger_id=999_999)


@pytest.mark.decision("decision-81")
async def test_a_user_who_may_only_view_workflows_reads_their_parts_too(
    client: Connected, stack: Stack, user: Callable[..., Signed]
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    made = await call(client, "create_workflow", name=name, enabled=False, triggers=[NEVER])
    [trigger] = cast("list[Json]", made["triggers"])

    async def read(right: str) -> object:
        token = user(right, "view_uisettings").token
        settings = server_settings(toolsets=["core", "workflows"])
        server = create_server(paperless_settings(stack.base_url, token), settings)
        arguments: Json = {"trigger_id": trigger["id"]}
        async with Client(server) as restricted:
            result = await restricted.call_tool("get_workflow", arguments, raise_on_error=False)
        return str(result.content) if result.is_error else result.structured_content

    held = cast("Json", cast("Json", await read("view_workflow"))["part"])
    alone = cast("Json", cast("Json", await read("view_workflowtrigger"))["part"])
    denied = await read("view_document")

    assert held["trigger"] == alone["trigger"] == trigger
    assert [w["name"] for w in cast("list[Json]", held["workflows"])] == [name]
    assert alone["workflows"] is None
    assert "may not perform this action" in str(denied)


def secret_workflow(stack: Stack, name: str, *, enabled: bool = False) -> Json:
    """A workflow with a webhook that holds secrets and an action this server cannot write."""
    hook = {"url": "https://user:planted@h.invalid/x", "headers": {"X-Token": "planted"}}
    actions: list[Json] = [
        {"type": 1, "assign_title": "one"},
        {"type": 4, "webhook": {**hook, "use_params": False, "body": "b"}},
        {"type": 5, "passwords": ["planted"]},
        {"type": 8, "ai_suggestion_fields": ["title"]},
    ]
    triggers = [{"type": 1, "filter_filename": NEVER["filename"]}, {"type": 5}]
    body = {"name": name, "enabled": enabled, "triggers": triggers, "actions": actions}
    _ = stack.admin.post("/api/workflows/", json=body).raise_for_status()
    [raw] = stored(stack, name)
    return raw


@pytest.mark.decision("decision-82")
async def test_single_parts_are_written_and_hidden_values_never_leave_paperless(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    made = secret_workflow(stack, name)
    title, hook, locked, suggests = cast("list[Json]", made["actions"])
    outputs: list[Json | str] = []

    async def part(**arguments: object) -> list[Json]:
        outputs.append(await call(client, "update_workflow_part", workflow=name, **arguments))
        [raw] = stored(stack, name)
        return cast("list[Json]", raw["actions"])

    added = await part(
        operation="add",
        action={"type": "assignment", "assign_tags": ["e2e-finance"]},
        position=1,
    )
    assert added[1:] == [title, hook, locked, suggests]
    fresh = added[0]
    moved = await part(operation="change", action_id=locked["id"], position=1)
    assert moved == [locked, fresh, title, hook, suggests]
    webhook = {"url": "https://h.invalid/x", "headers": {"X-Token": "(hidden)"}, "body": "c"}
    changed = await part(
        operation="change",
        action_id=hook["id"],
        action={"type": "webhook", "webhook": {**webhook, "use_params": False}},
    )
    assert changed[:3] + changed[4:] == [locked, fresh, title, suggests]
    kept = cast("Json", changed[3]["webhook"])
    assert (kept["url"], kept["headers"], kept["body"]) == (
        "https://user:planted@h.invalid/x",
        {"X-Token": "planted"},
        "c",
    )
    cleared = await part(operation="change", action_id=title["id"], clear=["assign_title"])
    assert cleared[2]["assign_title"] is None
    _ = await part(
        operation="add",
        trigger={
            "type": "added",
            "has_tags": ["e2e-finance"],
            "match": "n",
            "matching_algorithm": 1,
        },
    )
    [raw] = stored(stack, name)
    first, by_hand, new = cast("list[Json]", raw["triggers"])
    assert [first, by_hand] == made["triggers"]
    assert (new["type"], new["match"]) == (2, "n")
    gone = await part(operation="remove", trigger_id=by_hand["id"])
    assert gone == cleared

    arguments: Json = {"workflow": name, "operation": "remove", "action_id": hook["id"]}
    text = await refused(client, "update_workflow_part", **arguments)
    outputs.append(text)
    assert "lose for good what is stored hidden" in text
    assert "the value of 'X-Token', the credentials of its URL" in text
    [raw] = stored(stack, name)
    assert raw["actions"] == cleared
    left = await part(operation="remove", action_id=hook["id"], confirm=True)
    assert left == [locked, fresh, cleared[2], suggests]
    assert "planted" not in str(outputs)


@pytest.mark.decision("decision-82")
async def test_parts_of_an_enabled_workflow_are_confirmed_and_foreign_ones_refused(
    client: Connected, stack: Stack
) -> None:
    name, other = (f"{SCRATCH}{uuid.uuid4().hex[:8]}" for _ in range(2))
    made = secret_workflow(stack, name, enabled=True)
    theirs = secret_workflow(stack, other)
    [foreign, *_] = cast("list[Json]", theirs["actions"])
    tool = "update_workflow_part"
    title = {"type": "assignment", "assign_title": "x"}

    text = await refused(client, tool, workflow=name, operation="add", action=title)
    taken = await refused(
        client, tool, workflow=name, operation="remove", action_id=foreign["id"], confirm=True
    )
    unsafe = await refused(
        client,
        tool,
        workflow=other,
        operation="change",
        trigger_id=cast("list[Json]", theirs["triggers"])[0]["id"],
        clear=["filename"],
    )

    assert f"add an assignment action to the workflow '{name}'" in text
    assert "send a POST request to 'h.invalid', with credentials (not shown)" in text
    assert "holds no action" in taken
    assert "needs a path, filename or mailrule filter" in unsafe
    assert [stored(stack, n)[0] for n in (name, other)] == [made, theirs]
    _ = await call(client, tool, workflow=name, operation="add", action=title, confirm=True)
    assert len(cast("list[Json]", stored(stack, name)[0]["actions"])) == 5


@pytest.mark.decision("decision-82")
async def test_a_user_who_may_change_workflows_writes_parts_and_a_viewer_does_not(
    stack: Stack, client: Connected, user: Callable[..., Signed]
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    made = secret_workflow(stack, name)
    title, *rest = cast("list[Json]", made["actions"])
    assert (await call(client, "get_workflow", workflow=name))["name"] == name
    results_of: list[bool] = []
    for rights in (("view_workflow",), ("view_workflow", "change_workflow")):
        token = user(*rights, "view_uisettings").token
        settings = server_settings(toolsets=["core", "workflows"])
        server = create_server(paperless_settings(stack.base_url, token), settings)
        arguments: Json = {"workflow": name, "operation": "remove", "action_id": title["id"]}
        async with Client(server) as restricted:
            result = await restricted.call_tool(
                "update_workflow_part", arguments, raise_on_error=False
            )
        results_of.append(result.is_error)

    assert results_of == [True, False]
    [raw] = stored(stack, name)
    assert (raw["actions"], raw["triggers"]) == (rest, made["triggers"])


@pytest.mark.decision("decision-88")
async def test_a_workflow_is_explained_for_a_consumed_document(
    client: Connected, stack: Stack, user: Callable[..., Signed]
) -> None:
    prefix = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    tag = make(stack, "tags", {"name": f"{prefix}-tag"})
    assign = {"type": 1, "assign_tags": [tag]}

    def flow(name: str, *triggers: Json) -> None:
        body = {"name": f"{prefix}-{name}", "triggers": list(triggers), "actions": [assign]}
        _ = stack.admin.post("/api/workflows/", json=body).raise_for_status()

    flow("runs", {"type": 2, "filter_filename": f"{prefix}*"})
    # Not the inbox tag: Paperless gives it to every new document.
    flow("not", {"type": 2, "filter_has_tags": [stack.seeded.child_tag]})
    flow("later", {"type": 3, "match": "nothing-like-it", "matching_algorithm": 1})
    document = consume_text(stack.admin, f"{prefix} words")

    async def explain(name: str, connected: Connected = client) -> Json:
        arguments: Json = {"workflow": f"{prefix}-{name}", "document_id": document}
        return await call(connected, "explain_workflow", **arguments)

    try:
        ran, missed, later = [await explain(name) for name in ("runs", "not", "later")]
        token = user("view_workflow", "view_document", "view_uisettings").token
        unowned: Json = {"owner": None}
        _ = stack.admin.patch(f"/api/documents/{document}/", json=unowned).raise_for_status()
        settings = server_settings(toolsets=["core", "workflows"])
        server = create_server(paperless_settings(stack.base_url, token), settings)
        async with Client(server) as restricted:
            limited = await explain("runs", restricted)
    finally:
        delete_and_empty_trash(stack, [document])

    assert (ran["would_match_now"], ran["did_run"]) == ("yes", "yes"), ran
    certain = [f["said"] for f in cast("list[Json]", ran["log"]) if f["whose"] == "this document"]
    assert any("matched trigger" in str(said) for said in certain), ran
    assert any("was applied" in str(said) for said in certain), ran
    assert (missed["would_match_now"], missed["did_run"]) == ("no", "unknown"), missed
    said = [f for f in cast("list[Json]", missed["log"]) if f["whose"] == "this document"]
    assert [f["said"] for f in said] == ["the document did not match: none of the tags is there"], (
        missed
    )
    [check] = cast("list[Json]", cast("list[Json]", later["triggers"])[0]["checks"])
    assert (check["filter"], check["result"]) == ("match", "no")
    assert limited["did_run"] == "unknown"
    assert len(cast("list[str]", limited["not_checked"])) == 3
    assert limited["would_match_now"] == "yes"
