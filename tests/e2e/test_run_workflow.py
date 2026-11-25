"""run_workflow through the assembled server on the E2E Paperless (decision-75).

The workflows are made through the API of Paperless and have
only manual triggers, so they act on nothing but the documents of these tests.
"""

import uuid
from typing import TYPE_CHECKING

import pytest
from confirmation import YES, answering
from contract.conftest import delete_and_empty_trash, drop_orphaned_workflow_parts, live_in_trash
from fastmcp import Client
from harness import paperless_settings, server_settings
from services.test_workflow_runs import WEB_UI

from e2e.helpers import consume_text, num, results
from e2e.test_bulk import fields_of, make, raw, tags_of
from e2e.test_bulk_items import patch, share, user
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Json, Stack
    from e2e.test_bulk_items import Signed

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-75")]

__all__ = ["user"]  # the fixture

PREFIX = "contract-workflow-e2e-run-"


@pytest.fixture
def scratch(stack: Stack) -> Iterator[str]:
    """A unique name prefix; the workflows and tags that start with it are deleted afterwards."""
    prefix = f"{PREFIX}{uuid.uuid4().hex[:8]}-"
    yield prefix
    for row in results(stack.admin.get("/api/workflows/", params={"page_size": 100})):
        if str(row["name"]).startswith(prefix):
            _ = stack.admin.delete(f"/api/workflows/{row['id']}/")
    drop_orphaned_workflow_parts(stack)
    for row in results(stack.admin.get("/api/tags/", params={"name__istartswith": prefix})):
        _ = stack.admin.delete(f"/api/tags/{row['id']}/")


@pytest.fixture
def documents(stack: Stack, scratch: str) -> Iterator[list[int]]:
    """Three documents whose titles start with `scratch`."""
    created = [consume_text(stack.admin, f"{scratch}{n}") for n in range(3)]
    yield created
    delete_and_empty_trash(stack, created)


def server(stack: Stack, token: str) -> FastMCP:
    settings = server_settings(toolsets=["core", "workflows"])
    return create_server(paperless_settings(stack.base_url, token), settings)


async def run(stack: Stack, token: str, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool("run_workflow", arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, token: str, **arguments: object) -> str:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool("run_workflow", arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def workflow(
    stack: Stack,
    name: str,
    *actions: Json,
    triggers: list[Json] | None = None,
    enabled: bool = True,
) -> int:
    return make(
        stack,
        "workflows",
        {
            "name": name,
            "enabled": enabled,
            "triggers": triggers or [{"type": 5}],
            "actions": list(actions),
        },
    )


def assign(**settings: object) -> Json:
    return {"type": 1, **settings}


def task_state(stack: Stack, task_id: object) -> object:
    [row] = results(stack.admin.get("/api/tasks/", params={"task_id": str(task_id)}))
    return row["status"]


async def test_named_documents_run_after_a_confirmation_that_says_what_the_workflow_does(
    stack: Stack, scratch: str, documents: list[int]
) -> None:
    first, second, other = documents
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    number = workflow(
        stack, f"{scratch}flow", assign(assign_tags=[tag], assign_title="Ran {{ doc_id }}")
    )
    chosen: Json = {"workflow": number, "document_ids": [first, second]}

    preview = await run(stack, stack.admin_token, **chosen, dry_run=True)
    shown = await refused(stack, stack.admin_token, **chosen)
    untouched = [tags_of(stack, d) for d in documents]
    done = await run(stack, stack.admin_token, **chosen, confirm=True)

    assert (preview["started"], preview["selected_ids"]) == (False, [first, second])
    assert f"assign title 'Ran {{{{ doc_id }}}}', tags '{scratch}tag'" in shown
    assert f"{first} '{scratch}0'" in shown
    assert f"{second} '{scratch}1'" in shown
    assert not any(tag in found for found in untouched)
    assert (done["started"], done["failed"], done["unconfirmed_task_id"]) == (True, [], None)
    assert [tag in tags_of(stack, d) for d in documents] == [True, True, False]
    assert raw(stack, first)["title"] == f"Ran {first}"
    assert raw(stack, other)["title"] == f"{scratch}2"


async def test_the_filters_of_the_trigger_apply_unless_they_are_left_out(
    stack: Stack, scratch: str, documents: list[int]
) -> None:
    first, second, _ = documents
    wanted = make(stack, "tags", {"name": f"{scratch}wanted"})
    given = make(stack, "tags", {"name": f"{scratch}given"})
    patch(stack, first, tags=[wanted])
    number = workflow(
        stack,
        f"{scratch}flow",
        assign(assign_tags=[given]),
        triggers=[{"type": 5, "filter_has_tags": [wanted]}],
    )
    chosen: Json = {"workflow": number, "document_ids": [first, second], "confirm": True}

    filtered = await run(stack, stack.admin_token, **chosen)
    skipped = given not in tags_of(stack, second)
    whole = await run(stack, stack.admin_token, **chosen, apply_filters=False)

    assert "Paperless does not tell which" in str(filtered["message"])
    assert skipped
    assert given in tags_of(stack, first)
    assert str(whole["message"]).endswith("on 2 documents.")
    assert given in tags_of(stack, second)


async def test_a_trigger_as_the_web_ui_makes_it_has_no_filters(
    stack: Stack, scratch: str, documents: list[int]
) -> None:
    first, _, _ = documents
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    number = workflow(
        stack, f"{scratch}flow", assign(assign_tags=[tag]), triggers=[{**WEB_UI, "type": 5}]
    )

    done = await run(stack, stack.admin_token, workflow=number, document_ids=[first], confirm=True)

    assert str(done["message"]).endswith("on 1 document.")
    assert tag in tags_of(stack, first)


async def test_a_filter_runs_on_what_the_dialog_named_and_never_by_itself(
    stack: Stack, scratch: str, documents: list[int]
) -> None:
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    number = workflow(stack, f"{scratch}flow", assign(assign_tags=[tag]))
    chosen: Json = {"workflow": number, "filters": {"title_contains": scratch}, "confirm": True}
    seen: list[str] = []

    alone = await refused(stack, stack.admin_token, **chosen)
    untouched = not any(tag in tags_of(stack, d) for d in documents)
    async with Client(
        server(stack, stack.admin_token),
        elicitation_handler=answering("accept", YES, seen=seen),
    ) as client:
        _ = await client.call_tool("run_workflow", chosen)

    assert "With filters or saved_view alone, confirm=true is not enough" in alone
    assert untouched
    [shown] = seen
    assert all(f"{d} '{scratch}" in shown for d in documents)
    assert all(tag in tags_of(stack, d) for d in documents)


async def test_an_edited_workflow_is_asked_about_again(
    stack: Stack, scratch: str, documents: list[int]
) -> None:
    first, _, _ = documents
    tags = [make(stack, "tags", {"name": f"{scratch}{name}"}) for name in ("one", "two")]
    number = workflow(stack, f"{scratch}flow", assign(assign_tags=[tags[0]]))
    seen: list[str] = []

    async def edited() -> None:
        body = {"actions": [assign(assign_tags=[tags[1]])]}
        _ = stack.admin.patch(f"/api/workflows/{number}/", json=body).raise_for_status()

    async def once() -> None:
        if len(seen) == 1:
            await edited()

    async with Client(
        server(stack, stack.admin_token),
        elicitation_handler=answering("accept", YES, seen=seen, before=once),
    ) as client:
        _ = await client.call_tool("run_workflow", {"workflow": number, "document_ids": [first]})

    assert [f"{scratch}two" in message for message in seen] == [False, True]
    assert tags_of(stack, first) & set(tags) == {tags[1]}


async def test_an_unusable_workflow_is_refused(
    stack: Stack, scratch: str, documents: list[int]
) -> None:
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    off = workflow(stack, f"{scratch}off", assign(assign_tags=[tag]), enabled=False)
    added: Json = {"type": 2, "filter_has_tags": [tag]}
    automatic = workflow(stack, f"{scratch}auto", assign(assign_tags=[tag]), triggers=[added])
    chosen: Json = {"document_ids": documents, "confirm": True}

    disabled = await refused(stack, stack.admin_token, workflow=off, **chosen)
    by_itself = await refused(stack, stack.admin_token, workflow=automatic, **chosen)
    unknown = await refused(stack, stack.admin_token, workflow=f"{scratch}none", **chosen)

    assert "is not enabled" in disabled
    assert "has no manual trigger" in by_itself
    assert f"{scratch}none" in unknown
    assert not any(tag in tags_of(stack, d) for d in documents)


async def test_a_run_that_fails_on_some_documents_names_them(
    stack: Stack, scratch: str, documents: list[int]
) -> None:
    """The trigger decides which document fails: the others are skipped, and not named."""
    first, second, third = documents
    wanted = make(stack, "tags", {"name": f"{scratch}wanted"})
    given = make(stack, "tags", {"name": f"{scratch}given"})
    patch(stack, second, tags=[wanted])
    [field] = results(
        stack.admin.get("/api/custom_fields/", params={"name__iexact": "e2e-integer"})
    )
    broken = str(num(field["id"]))
    number = workflow(
        stack,
        f"{scratch}flow",
        assign(assign_tags=[given]),
        assign(assign_custom_fields=[int(broken)], assign_custom_fields_values={broken: "abc"}),
        triggers=[{"type": 5, "filter_has_tags": [wanted]}],
    )
    chosen: Json = {"workflow": number, "document_ids": documents, "confirm": True}

    done = await run(stack, stack.admin_token, **chosen)
    partly = [given in tags_of(stack, d) for d in documents]
    failed = await refused(stack, stack.admin_token, **chosen, apply_filters=False)

    assert done["failed"] == [second]
    assert str(done["message"]).startswith(
        f"The workflow '{scratch}flow' (id {number}) failed on 1"
    )
    # What came before the action that failed is done.
    assert partly == [False, True, False]
    assert "failed on 3 of 3 documents" in failed
    assert "do not run it again" in failed
    assert fields_of(stack, first) == fields_of(stack, third) == {}


async def test_documents_move_to_the_trash_and_what_cannot_be_sent_fails_nothing(
    stack: Stack, scratch: str, documents: list[int]
) -> None:
    first, second, _ = documents
    mail = {"subject": "s", "body": "b", "to": "a@example.org"}
    # Nothing is sent: the stack has no mail server, and nothing listens on the port.
    hook = {"url": "http://127.0.0.1:9/never", "use_params": False}
    sending = workflow(
        stack, f"{scratch}send", {"type": 3, "email": mail}, {"type": 4, "webhook": hook}
    )
    trashing = workflow(stack, f"{scratch}trash", {"type": 6})

    shown = await refused(stack, stack.admin_token, workflow=sending, document_ids=[first])
    sent = await run(stack, stack.admin_token, workflow=sending, document_ids=[first], confirm=True)
    gone = await run(
        stack, stack.admin_token, workflow=trashing, document_ids=[first, second], confirm=True
    )

    assert "send an e-mail to 'a@example.org'" in shown
    assert "send a POST request to 'http://127.0.0.1:9'" in shown
    assert sent["failed"] == []
    assert "only the Paperless logs show it" in str(sent["message"])
    assert (gone["failed"], gone["unconfirmed_task_id"]) == ([], None)
    assert "restore_from_trash" in str(gone["message"])
    assert live_in_trash(stack, first)
    assert live_in_trash(stack, second)


async def test_a_restricted_user_runs_it_on_own_documents_only(
    stack: Stack, scratch: str, documents: list[int], user: Callable[..., Signed]
) -> None:
    own, shared, hidden = documents
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    number = workflow(stack, f"{scratch}flow", assign(assign_tags=[tag]))
    rights = ("view_document", "change_document", "view_uisettings")
    blind = user(*rights)
    runner = user(*rights, "view_workflow", "view_paperlesstask")
    patch(stack, own, owner=runner.id)
    share(stack, shared, runner.id)

    without = await refused(
        stack, blind.token, workflow=number, document_ids=[shared], confirm=True
    )
    foreign = await refused(
        stack, runner.token, workflow=number, document_ids=[own, shared], confirm=True
    )
    unseen = await refused(
        stack, runner.token, workflow=number, document_ids=[own, hidden], confirm=True
    )
    unable = user("view_document", "view_workflow", "view_uisettings")
    patch(stack, hidden, owner=unable.id)
    lacking = await refused(
        stack, unable.token, workflow=number, document_ids=[hidden], confirm=True
    )
    untouched = not any(tag in tags_of(stack, d) for d in documents)
    done = await run(stack, runner.token, workflow=number, document_ids=[own], confirm=True)

    assert "The Paperless user may not perform this action." in without
    assert "You may not change or do not own" in foreign
    assert str(hidden) in unseen
    assert "You lack the permission to change documents" in lacking
    assert untouched
    assert done["started"] is True
    assert tag in tags_of(stack, own)


async def test_a_user_who_may_not_view_tasks_is_told_that_the_end_is_not_known(
    stack: Stack, scratch: str, documents: list[int], user: Callable[..., Signed]
) -> None:
    own, _, _ = documents
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    number = workflow(stack, f"{scratch}flow", assign(assign_tags=[tag]))
    runner = user("view_document", "change_document", "view_workflow")
    patch(stack, own, owner=runner.id)

    done = await run(stack, runner.token, workflow=number, document_ids=[own], confirm=True)

    assert done["unconfirmed_task_id"] is not None
    assert "do not run it again" in str(done["message"])
    assert task_state(stack, done["unconfirmed_task_id"]) in {"pending", "started", "success"}
