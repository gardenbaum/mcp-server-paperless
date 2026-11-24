"""The trash toolset through the assembled server on the E2E Paperless, on documents of its own.

Titles start with `plmcp27-`; they are plain text, which skips OCR. The shared stack's trash holds
entries of other tests, so nothing here empties the whole trash: `all=true` is only called without
confirm (it must refuse and name what it would destroy), and what it does with confirm is tested
on the fake (tests/services/test_trash.py). Every document a test made is deleted again at its end
and its trash entry emptied, also when it fails.
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from fastmcp import Client
from harness import paperless_settings, server_settings
from mcp.types import BlobResourceContents, EmbeddedResource

from e2e.helpers import consume_text, num, parse, results, seq
from e2e.uploading import Scope as Users
from e2e.uploading import output, text, unique
from mcp_server_paperless.server import create_server
from mcp_server_paperless.services.trash import MAX_ALL

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP
    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def document(self, label: str) -> tuple[int, str]:
        title = f"plmcp27-{label}-{uuid.uuid4().hex[:8]}"
        created = consume_text(self.stack.admin, title)
        self.ids.append(created)
        return created, title


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token),
        server_settings(toolsets=["core", "trash"], read_only=read_only),
    )


async def run(client: Client[FastMCPTransport], tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(client: Client[FastMCPTransport], tool: str, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def listed(client: Client[FastMCPTransport], wanted: set[int]) -> dict[int, Json]:
    """The trash entries with these ids, page by page until all are found."""
    found: dict[int, Json] = {}
    page: object = 1
    while page is not None and not wanted <= found.keys():
        output = await run(client, "list_trash", page=page, page_size=100)
        found |= {
            int(cast("int", e["id"])): e
            for e in cast("list[Json]", output["entries"])
            if e["id"] in wanted
        }
        page = output["next_page"]
    return found


def note_count(stack: Stack, document: int) -> int:
    return len(seq(parse(stack.admin.get(f"/api/documents/{document}/notes/"))))


async def test_a_deleted_document_is_listed_restored_and_emptied_only_with_confirm(
    stack: Stack, scope: Scope
) -> None:
    kept, kept_title = scope.document("kept")
    gone, gone_title = scope.document("gone")
    other, other_title = scope.document("other")
    _ = stack.admin.post(f"/api/documents/{kept}/notes/", json={"note": "plmcp27 note"})
    async with Client(server(stack, stack.admin_token)) as admin:
        for document in (kept, gone, other):
            _ = await run(admin, "delete_document", document_id=document, confirm=True)

        entries = await listed(admin, {kept, gone, other})
        assert {i: e["title"] for i, e in entries.items()} == {
            kept: kept_title,
            gone: gone_title,
            other: other_title,
        }
        assert all(e["version_of"] is None for e in entries.values())

        restored = await run(admin, "restore_from_trash", document_ids=[kept])
        assert restored["message"] == (
            f"Restored 1 document from the trash: document {kept} {kept_title!r}."
        )
        [entry] = cast("list[Json]", restored["entries"])
        assert str(entry["web_url"]).endswith(f"/documents/{kept}/details")
        assert not live_in_trash(stack, kept)
        assert note_count(stack, kept) == 1
        assert "Not in the trash" in await refused(admin, "restore_from_trash", document_ids=[kept])

        # Emptying needs a selection and confirm, in both forms; nothing changed so far.
        assert "Name what to empty" in await refused(admin, "empty_trash", confirm=True)
        asked = await refused(admin, "empty_trash", document_ids=[gone, other])
        assert f"document {gone} {gone_title!r}" in asked
        assert f"document {other} {other_title!r}" in asked
        assert "for good" in asked
        # all=true is refused without confirm either way; which refusal depends on the size of
        # the shared stack's trash, which is read first.
        total = (await run(admin, "list_trash", page_size=1))["count"]
        everything = await refused(admin, "empty_trash", all=True)
        if cast("int", total) > MAX_ALL:
            assert f"all=true handles at most {MAX_ALL} at once" in everything
        else:
            assert "Nothing was changed. Confirming would permanently delete ALL " in everything
        assert live_in_trash(stack, gone)
        assert live_in_trash(stack, other)

        emptied = await run(admin, "empty_trash", document_ids=[gone], confirm=True)
        assert (emptied["document_ids"], emptied["count"]) == ([gone], 1)
    assert not live_in_trash(stack, gone)
    assert live_in_trash(stack, other)
    assert stack.admin.get(f"/api/documents/{gone}/").status_code == 404

    # Someone who cannot see an entry cannot empty it either.
    async with Client(server(stack, stack.reader_token)) as reader:
        hidden = await refused(reader, "empty_trash", document_ids=[other], confirm=True)
    assert f"Not in the trash: {other}" in hidden
    assert live_in_trash(stack, other)


async def test_read_only_mode_offers_only_the_list(stack: Stack) -> None:
    async with Client(server(stack, stack.admin_token, read_only=True)) as client:
        tools = {t.name for t in await client.list_tools()}
        result = await client.call_tool("list_trash", {"page_size": 1})

    assert {"list_trash"} <= tools
    assert not tools & {"restore_from_trash", "empty_trash"}
    assert result.structured_content is not None
    assert result.structured_content["page"] == 1


# --- filters, looking into entries, and a restricted user (PLMCP-78) ------------------------------


@pytest.fixture
def users(stack: Stack) -> Iterator[Users]:
    made = Users(stack)
    yield made
    made.clean_up()


def ids(listed: Json) -> set[int]:
    return {num(e["id"]) for e in cast("list[Json]", listed["entries"])}


async def test_every_field_of_a_row_is_known(stack: Stack, users: Users) -> None:
    """What Paperless 3.5.1 really sends for an entry is what the adapter reads (criterion 1)."""
    document = users.document(unique())
    _ = stack.admin.delete(f"/api/documents/{document}/").raise_for_status()

    rows = results(stack.admin.get("/api/trash/", params={"page_size": 1000}))
    [row] = [r for r in rows if r["id"] == document]
    entry = output(await users.call("get_trash_entry", document_id=document, text=True))

    renamed = {"root_document": "version_of", "content": "text"}
    assert {renamed.get(name, name) for name in row} <= entry.keys()


async def test_the_list_is_narrowed_by_every_filter(stack: Stack, users: Users) -> None:
    mark = f"plmcp78-{uuid.uuid4().hex}"
    kept, gone, other = (users.document(unique(), f"{mark}-{n}") for n in ("kept", "gone", "other"))
    kinds = results(stack.admin.get("/api/document_types/"))
    _ = stack.admin.patch(
        f"/api/documents/{gone}/", json={"document_type": kinds[0]["id"]}
    ).raise_for_status()
    version = num(output(await users.upload(unique(), document_id=kept))["version_id"])
    _ = output(
        await users.call(
            "delete_document_version", document_id=kept, version_id=version, confirm=True
        )
    )
    for number in (gone, other):
        _ = output(await users.call("delete_document", document_id=number, confirm=True))

    async def found(**wanted: object) -> set[int]:
        listed = output(await users.call("list_trash", None, page_size=100, **wanted))
        assert num(listed["count"]) == len(ids(listed))
        return ids(listed)

    today = datetime.now(UTC).date()
    around = {
        "deleted_from": (today - timedelta(days=1)).isoformat(),
        "deleted_to": (today + timedelta(days=1)).isoformat(),
    }
    assert await found(title=mark.upper()) == {version, gone, other}
    assert await found(title=mark, kind="document") == {gone, other}
    assert await found(title=mark, kind="version") == {version}
    assert await found(title=mark, document_type=kinds[0]["name"]) == {gone}
    assert await found(title=mark, **around) == {version, gone, other}
    assert await found(title=mark, deleted_to="2020-01-01") == set()
    assert await found(ids=[gone, other, version], title=f"{mark}-other") == {other}
    _ = stack.admin.patch(
        f"/api/documents/{kept}/", json={"document_type": kinds[0]["id"]}
    ).raise_for_status()
    assert await found(title=mark, document_type=kinds[0]["id"]) == {gone, version}


async def test_text_thumbnail_and_file_of_an_entry_are_read(users: Users) -> None:
    content = unique()
    document = users.document(content)
    _ = output(await users.upload(unique(), document_id=document))
    _ = output(await users.call("delete_document", document_id=document, confirm=True))

    entry = output(await users.call("get_trash_entry", document_id=document, text=True))
    file = await users.call("get_trashed_file", document_id=document)
    part = await users.call("get_trashed_file", document_id=document, offset=0, max_bytes=7)
    image = await users.call("get_trashed_file", document_id=document, file="thumbnail")
    archive = await users.call("get_trashed_file", document_id=document, file="archive")

    assert cast("Json", entry["text"])["content"] == content.decode()
    assert "restore_from_trash" in str(entry["limits"])
    for answer, expected in ((file, content), (part, content[:7])):
        assert not answer.is_error, text(answer)
        block = answer.content[1]
        assert isinstance(block, EmbeddedResource)
        assert isinstance(block.resource, BlobResourceContents)
        assert base64.b64decode(block.resource.blob) == expected
    assert not image.is_error
    assert len(image.content) == 2
    assert "has no archive version" in text(archive)


async def test_a_filter_only_shows_and_the_shown_ids_are_restored_or_deleted(
    stack: Stack, users: Users
) -> None:
    mark = f"plmcp78-{uuid.uuid4().hex}"
    back, gone = (users.document(unique(), f"{mark}-{n}") for n in ("back", "gone"))
    for number in (back, gone):
        _ = output(await users.call("delete_document", document_id=number, confirm=True))

    shown = text(await users.call("restore_from_trash", filter={"title": f"{mark}-back"}))
    asked = text(await users.call("empty_trash", filter={"title": f"{mark}-gone"}, confirm=True))
    assert f"document_ids=[{back}]" in shown
    assert f"document_ids=[{gone}] and confirm=true" in asked
    assert live_in_trash(stack, back)
    assert live_in_trash(stack, gone)

    restored = output(await users.call("restore_from_trash", document_ids=[back]))
    emptied = output(await users.call("empty_trash", document_ids=[gone], confirm=True))

    assert [e["outcome"] for e in cast("list[Json]", restored["entries"])] == ["restored"]
    assert [e["outcome"] for e in cast("list[Json]", emptied["entries"])] == ["deleted"]
    assert not live_in_trash(stack, back)
    assert not live_in_trash(stack, gone)


async def test_all_or_none_and_the_id_that_blocks_is_named(stack: Stack, users: Users) -> None:
    document = users.document(unique())
    version = num(output(await users.upload(unique(), document_id=document))["version_id"])
    ready, alive = users.document(unique()), users.document(unique())
    for number in (document, ready):
        _ = output(await users.call("delete_document", document_id=number, confirm=True))

    for tool, confirm in (("restore_from_trash", {}), ("empty_trash", {"confirm": True})):
        refused = text(
            await users.call(tool, None, document_ids=[ready, alive, version, 999_999], **confirm)
        )
        assert f"Not in the trash: {alive}, {version}, 999999." in refused
        assert f"{alive} is not deleted." in refused
        assert f"{version} is a version of document {document}" in refused
        assert live_in_trash(stack, ready)


async def test_a_restricted_user_sees_and_changes_only_owned_and_unowned_entries(
    stack: Stack, users: Users
) -> None:
    owned, unowned, shared, hidden = (users.document(unique()) for _ in range(4))
    token = users.user("view_document", "delete_document")
    viewer = users.user("view_document")
    user = users.users[0]
    grant: Json = {"view": {"users": [user], "groups": []}, "change": {"users": [], "groups": []}}
    changes: list[tuple[int, Json]] = [
        (owned, {"owner": user}),
        (unowned, {"owner": None}),
        (shared, {"set_permissions": grant}),
    ]
    for number, change in changes:
        _ = stack.admin.patch(f"/api/documents/{number}/", json=change).raise_for_status()
    everything = [owned, unowned, shared, hidden]
    for number in everything:
        _ = stack.admin.delete(f"/api/documents/{number}/").raise_for_status()

    listed = output(await users.call("list_trash", token, ids=everything))
    assert ids(listed) == {owned, unowned}
    for number in (shared, hidden):
        for tool in ("get_trash_entry", "get_trashed_file"):
            assert f"Not in the trash: {number}" in text(
                await users.call(tool, token, document_id=number)
            )
    refused = text(await users.call("restore_from_trash", token, document_ids=everything))
    assert f"Not in the trash: {shared}, {hidden}." in refused
    assert f"{shared} is in the trash, but listed only for its owner." in refused
    # Without the global permission Paperless refuses and names no id.
    denied = text(await users.call("restore_from_trash", viewer, document_ids=[unowned]))
    assert "Paperless does not say which id it refuses" in denied
    assert all(live_in_trash(stack, number) for number in everything)

    restored = output(await users.call("restore_from_trash", token, document_ids=[owned, unowned]))
    assert {e["outcome"] for e in cast("list[Json]", restored["entries"])} == {"restored"}
