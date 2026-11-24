"""The bulk toolset through the assembled server on the E2E Paperless, on objects of its own.

Documents and taxonomy are created by the tests and removed again (documents with their trash
entries), so nothing is left behind and no seeded object is changed.
"""

import time
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from confirmation import YES, answering
from contract.conftest import delete_and_empty_trash, live_in_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume_text, num, obj, parse, results, seq
from e2e.test_read_documents import bare_documents
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Json, Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

PREFIX = "plmcp22-"
PATHS = ("tags", "correspondents", "document_types", "storage_paths", "custom_fields")


def new_documents(stack: Stack, count: int) -> list[int]:
    """New plain text documents of the admin (no OCR, so consumption takes about a second)."""
    return [consume_text(stack.admin, f"{PREFIX}{n}-{uuid.uuid4().hex}") for n in range(count)]


@pytest.fixture(scope="module")
def documents(stack: Stack) -> Iterator[list[int]]:
    """Two documents shared by the tests that leave them as they found them (module scope).

    Deleted and removed from the trash again at the end.
    """
    created = new_documents(stack, 2)
    yield created
    delete_and_empty_trash(stack, created)


@pytest.fixture
def trashable(stack: Stack) -> Iterator[list[int]]:
    """Two documents of their own for the test that moves them to the trash."""
    created = new_documents(stack, 2)
    yield created
    delete_and_empty_trash(stack, created)


@pytest.fixture
def scratch(stack: Stack) -> Iterator[str]:
    """A unique name prefix; every object whose name starts with it is deleted afterwards."""
    prefix = f"{PREFIX}{uuid.uuid4().hex[:8]}-"
    yield prefix
    for kind in PATHS:
        found = stack.admin.get(f"/api/{kind}/", params={"name__istartswith": prefix})
        for row in results(found):
            _ = stack.admin.delete(f"/api/{kind}/{row['id']}/")


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    settings = server_settings(toolsets=["core", "bulk"], read_only=read_only)
    return create_server(paperless_settings(stack.base_url, token), settings)


async def run(stack: Stack, token: str, tool: str, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, token: str, tool: str, **arguments: object) -> str:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def make(stack: Stack, kind: str, body: Json) -> int:
    return num(obj(parse(stack.admin.post(f"/api/{kind}/", json=body).raise_for_status()))["id"])


def raw(stack: Stack, document: int) -> Json:
    return obj(parse(stack.admin.get(f"/api/documents/{document}/").raise_for_status()))


def tags_of(stack: Stack, document: int) -> set[int]:
    return {num(tag) for tag in seq(raw(stack, document)["tags"])}


def fields_of(stack: Stack, document: int) -> dict[int, object]:
    values = (obj(v) for v in seq(raw(stack, document)["custom_fields"]))
    return {num(v["field"]): v["value"] for v in values}


async def test_tags_are_previewed_confirmed_and_follow_the_hierarchy(
    stack: Stack, documents: list[int], scratch: str
) -> None:
    parent = make(stack, "tags", {"name": f"{scratch}parent"})
    child = make(stack, "tags", {"name": f"{scratch}child", "parent": parent})
    before = [tags_of(stack, d) for d in documents]
    add = {"op": "add_tags", "tags": [f"{scratch}child"]}

    preview = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation=add,
        dry_run=True,
    )
    unconfirmed = await refused(
        stack, stack.admin_token, "bulk_edit_documents", document_ids=documents, operation=add
    )

    assert (preview["dry_run"], preview["affected_count"], preview["unchanged_count"]) == (
        True,
        2,
        0,
    )
    assert len(cast("list[object]", preview["sample"])) == 2
    assert "Nothing was changed" in unconfirmed
    assert "confirm=true" in unconfirmed
    assert [tags_of(stack, d) for d in documents] == before

    done = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation=add,
        confirm=True,
    )

    assert (done["applied"], done["affected_count"]) == (True, 2)
    for document, original in zip(documents, before, strict=True):
        assert tags_of(stack, document) == original | {parent, child}

    blocked = await refused(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "remove_tags", "tags": [f"{scratch}parent"]},
        confirm=True,
    )
    assert "also removes its child tags" in blocked
    assert all({parent, child} <= tags_of(stack, d) for d in documents)

    both = {"op": "remove_tags", "tags": [f"{scratch}parent", f"{scratch}child"]}
    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation=both,
        confirm=True,
    )
    assert all(not ({parent, child} & tags_of(stack, d)) for d in documents)


async def test_references_and_custom_fields_change_without_touching_the_rest(
    stack: Stack, documents: list[int], scratch: str
) -> None:
    correspondent = make(stack, "correspondents", {"name": f"{scratch}corr"})
    text = make(stack, "custom_fields", {"name": f"{scratch}text", "data_type": "string"})
    number = make(stack, "custom_fields", {"name": f"{scratch}number", "data_type": "integer"})
    first, second = documents

    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "set_correspondent", "correspondent": f"{scratch}corr"},
        confirm=True,
    )
    assert [raw(stack, d)["correspondent"] for d in documents] == [correspondent] * 2

    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "modify_custom_fields", "values": {f"{scratch}text": "a"}},
        confirm=True,
    )
    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=[first],
        operation={"op": "modify_custom_fields", "values": {f"{scratch}number": 5}},
        confirm=True,
    )
    assert fields_of(stack, first) == {text: "a", number: 5}
    assert fields_of(stack, second) == {text: "a"}

    removed = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "modify_custom_fields", "remove": [f"{scratch}text"]},
        confirm=True,
    )
    assert removed["affected_count"] == 2
    assert fields_of(stack, first) == {number: 5}

    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "set_correspondent", "correspondent": None},
        confirm=True,
    )
    assert [raw(stack, d)["correspondent"] for d in documents] == [None, None]


async def test_documents_go_to_the_trash_and_reprocessing_is_queued(
    stack: Stack, trashable: list[int]
) -> None:
    again = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=trashable[:1],
        operation={"op": "reprocess"},
        confirm=True,
    )
    assert again["applied"] is True

    unconfirmed = await refused(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=trashable,
        operation={"op": "delete"},
    )
    assert "trash" in unconfirmed
    assert not any(live_in_trash(stack, d) for d in trashable)

    done = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=trashable,
        operation={"op": "delete"},
        confirm=True,
    )

    assert done["affected_count"] == 2
    assert all(live_in_trash(stack, d) for d in trashable)
    assert all(stack.admin.get(f"/api/documents/{d}/").status_code == 404 for d in trashable)


async def test_a_missing_document_and_a_restricted_user_change_nothing(
    stack: Stack, documents: list[int], scratch: str
) -> None:
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    add = {"op": "add_tags", "tags": [tag]}
    public = stack.seeded.public_document
    # The restricted user sees the shared seed taxonomy only.
    inbox = {"op": "remove_tags", "tags": [stack.seeded.inbox_tag]}
    seeded_tags = tags_of(stack, public)

    missing = await refused(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=[*documents, 999_999],
        operation=add,
        confirm=True,
    )
    denied = await refused(
        stack,
        stack.reader_token,
        "bulk_edit_documents",
        document_ids=[public],
        operation=inbox,
        confirm=True,
    )
    preview = await run(
        stack,
        stack.reader_token,
        "bulk_edit_documents",
        document_ids=[public],
        operation=inbox,
        dry_run=True,
    )

    assert "999999" in missing
    assert "nothing was changed" in denied
    assert (preview["dry_run"], preview["affected_count"]) == (True, 1)
    assert all(tag not in tags_of(stack, d) for d in documents)
    assert tags_of(stack, public) == seeded_tags


@pytest.mark.decision("decision-71")
async def test_a_filter_changes_what_the_dialog_named(stack: Stack, scratch: str) -> None:
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    created = [consume_text(stack.admin, f"{scratch}{n}") for n in (1, 2)]
    try:
        seen: list[str] = []
        async with Client(
            server(stack, stack.admin_token),
            elicitation_handler=answering("accept", YES, seen=seen),
        ) as client:
            result = await client.call_tool(
                "bulk_edit_documents",
                {
                    "filters": {"title_contains": scratch},
                    "operation": {"op": "add_tags", "tags": [tag]},
                    "confirm": True,
                },
            )

        assert result.structured_content is not None
        output: Json = result.structured_content
        assert set(seq(output["selected_ids"])) == set(created)
        [shown] = seen
        assert all(f"{number} '{scratch}" in shown for number in created)
        assert all(tag in tags_of(stack, number) for number in created)
    finally:
        delete_and_empty_trash(stack, created)


@pytest.mark.decision("decision-71")
async def test_without_dialogs_only_ids_that_still_match_the_filter_change(
    stack: Stack, scratch: str
) -> None:
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    first, second = created = [consume_text(stack.admin, f"{scratch}{n}") for n in (1, 2)]
    chosen: Json = {
        "filters": {"title_contains": scratch, "tags_none": [f"{scratch}tag"]},
        "operation": {"op": "add_tags", "tags": [tag]},
        "confirm": True,
    }
    admin = stack.admin_token
    try:
        shown = await refused(stack, admin, "bulk_edit_documents", **chosen)
        assert all(f"{number} '{scratch}" in shown for number in created)
        assert not any(tag in tags_of(stack, number) for number in created)

        # Someone else tags one of them in between.
        body = {"tags": [tag]}
        _ = stack.admin.patch(f"/api/documents/{first}/", json=body).raise_for_status()
        changed = await refused(stack, admin, "bulk_edit_documents", document_ids=created, **chosen)
        assert f"no longer matches: {first}" in changed
        assert tag not in tags_of(stack, second)

        done = await run(stack, admin, "bulk_edit_documents", document_ids=[second], **chosen)
        assert (done["applied"], done["selected_ids"], done["remaining"]) == (True, [second], 0)
        assert tag in tags_of(stack, second)
        # What was changed no longer matches: the same call changes nothing again.
        nothing = await run(stack, admin, "bulk_edit_documents", **chosen)
        assert (nothing["matched"], nothing["applied"]) == (0, False)
    finally:
        delete_and_empty_trash(stack, created)


@pytest.mark.decision("decision-71")
async def test_a_hundred_documents_change_per_call_and_the_same_filter_goes_on(
    stack: Stack, scratch: str, capsys: pytest.CaptureFixture[str]
) -> None:
    number = make(stack, "correspondents", {"name": f"{scratch}corr"})
    with bare_documents(101, scratch, "1992-01-01") as name:
        chosen: Json = {
            "filters": {"title_contains": name},
            "operation": {"op": "set_correspondent", "correspondent": number},
        }
        done: list[Json] = []
        for _round in (1, 2):
            preview = await run(
                stack, stack.admin_token, "bulk_edit_documents", **chosen, dry_run=True
            )
            started = time.monotonic()
            done.append(
                await run(
                    stack,
                    stack.admin_token,
                    "bulk_edit_documents",
                    **chosen,
                    document_ids=preview["selected_ids"],
                    confirm=True,
                )
            )
            took = time.monotonic() - started
            with capsys.disabled():
                print(f"\nbulk_edit_documents: {preview['affected_count']} in {took:.2f} s")  # noqa: T201 -- the time of P6, read from the log
        last = await run(stack, stack.admin_token, "bulk_edit_documents", **chosen, dry_run=True)

    assert [(r["affected_count"], r["remaining"]) for r in done] == [(100, 1), (1, 0)]
    assert (last["matched"], last["affected_count"], last["unchanged_count"]) == (101, 0, 101)


@pytest.mark.decision("decision-71")
async def test_a_restricted_user_changes_nothing_by_filter_and_sees_no_hidden_document(
    stack: Stack,
) -> None:
    public, inbox = stack.seeded.public_document, stack.seeded.inbox_tag
    before = tags_of(stack, public)
    chosen: Json = {
        "filters": {"title_contains": "e2e-p"},
        "operation": {"op": "remove_tags", "tags": [inbox]},
    }

    preview = await run(stack, stack.reader_token, "bulk_edit_documents", **chosen, dry_run=True)
    denied = await refused(
        stack,
        stack.reader_token,
        "bulk_edit_documents",
        **chosen,
        document_ids=[public],
        confirm=True,
    )

    assert (preview["matched"], preview["selected_ids"]) == (1, [public])
    # Paperless's own check: the user may change the document, but lacks the global permission.
    assert preview["blocked"] == []
    assert "Paperless refused the bulk edit; it checks every selected document first" in denied
    assert "Seen after that" not in denied
    assert tags_of(stack, public) == before


@pytest.mark.decision("decision-69")
async def test_paperless_takes_a_filter_it_does_not_understand_as_none(stack: Stack) -> None:
    """P2, why Paperless's own filters are never sent. Only read: nothing is changed."""
    inbox = stack.seeded.inbox_tag

    def tagged(**filters: object) -> int:
        body = {"all": True, "filters": filters}
        answer = stack.admin.post("/api/documents/selection_data/", json=body).raise_for_status()
        rows = [obj(row) for row in seq(obj(parse(answer))["selected_tags"])]
        return next(num(row["document_count"]) for row in rows if row["id"] == inbox)

    everything = tagged()
    assert everything > 0
    assert tagged(title__istartswith="no document has this title") == 0
    assert tagged(tags__id__all="abc") == everything
    assert tagged(tags__id__all="") == everything
    assert tagged(titel__istartswith="no document has this title") == everything


async def test_taxonomy_permissions_are_set_and_the_objects_deleted_with_confirm(
    stack: Stack, scratch: str
) -> None:
    first = make(stack, "correspondents", {"name": f"{scratch}one"})
    second = make(stack, "correspondents", {"name": f"{scratch}two"})
    reader = num(
        results(stack.admin.get("/api/users/", params={"username__iexact": "e2e-reader"}))[0]["id"]
    )
    group = num(
        results(stack.admin.get("/api/groups/", params={"name__iexact": "e2e-group"}))[0]["id"]
    )
    selection = {"kind": "correspondent", "items": [f"{scratch}one", f"{scratch}two"]}
    grants = {
        "op": "set_permissions",
        "view_users": ["e2e-reader"],
        "view_groups": ["e2e-group"],
    }

    def full(item: int) -> Json:
        found = stack.admin.get(f"/api/correspondents/{item}/", params={"full_perms": "true"})
        return obj(parse(found.raise_for_status()))

    preview = await run(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=grants, dry_run=True
    )
    unconfirmed = await refused(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=grants
    )
    assert (preview["dry_run"], preview["affected_count"]) == (True, 2)
    assert "Nothing was changed" in unconfirmed
    assert obj(obj(full(first)["permissions"])["view"])["users"] == []

    done = await run(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=grants, confirm=True
    )

    assert done["applied"] is True
    for item in (first, second):
        view = obj(obj(full(item)["permissions"])["view"])
        assert (view["users"], view["groups"]) == ([reader], [group])
    visible = {r["id"] for r in results(stack.reader.get("/api/correspondents/"))}
    assert {first, second} <= visible

    delete = {"op": "delete"}
    text = await refused(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=delete
    )
    gone = await run(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=delete, confirm=True
    )

    assert "delete 2 correspondents" in text
    assert gone["affected_count"] == 2
    assert [stack.admin.get(f"/api/correspondents/{i}/").status_code for i in (first, second)] == [
        404,
        404,
    ]


async def test_a_tag_deletion_names_the_child_tags_that_go_with_it(
    stack: Stack, scratch: str
) -> None:
    parent = make(stack, "tags", {"name": f"{scratch}parent"})
    child = make(stack, "tags", {"name": f"{scratch}child", "parent": parent})

    preview = await run(
        stack,
        stack.admin_token,
        "bulk_edit_taxonomy",
        kind="tag",
        items=[f"{scratch}parent"],
        operation={"op": "delete"},
        dry_run=True,
    )
    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_taxonomy",
        kind="tag",
        items=[parent],
        operation={"op": "delete"},
        confirm=True,
    )

    assert preview["also_deleted"] == [f"{scratch}child"]
    assert stack.admin.get(f"/api/tags/{child}/").status_code == 404


async def test_a_read_only_server_offers_neither_bulk_tool(stack: Stack) -> None:
    async with Client(server(stack, stack.admin_token, read_only=True)) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint({"bulk_edit_documents", "bulk_edit_taxonomy"})
