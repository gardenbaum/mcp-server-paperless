"""The next ASN, counts, suggest_words and test_storage_path on Paperless 3.5.1 (PLMCP-83.2).

Every call runs against the installed server, over stdio and over HTTP.
"""

import contextlib
import uuid
from typing import TYPE_CHECKING, Literal, cast

import pytest
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from fastmcp.client.transports import ClientTransport, StdioTransport, StreamableHttpTransport

from e2e.helpers import consume_text, num, obj, parse, rebuild_search_index
from e2e.test_http import MCP_TOKEN, running
from e2e.test_stdio import console_script

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator

    from fastmcp.client.client import CallToolResult

    from e2e.helpers import Json, Stack

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-100"),
    pytest.mark.parametrize("transport", ["stdio", "http"]),
]

type Connected = Client[ClientTransport]
type Transport = Literal["stdio", "http"]
TOOLSETS = "core,bulk"


@contextlib.asynccontextmanager
async def connected(
    stack: Stack, transport: Transport, *, token: str | None = None
) -> AsyncGenerator[Connected]:
    env = {"PAPERLESS_TOKEN": token or stack.admin_token, "PAPERLESS_MCP_TOOLSETS": TOOLSETS}
    if transport == "stdio":
        started = StdioTransport(
            console_script(),
            [],
            env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_MCP_READ_ONLY": "true", **env},
            keep_alive=False,
        )
        async with Client[ClientTransport](started) as client:
            yield client
        return
    with running(stack, PAPERLESS_MCP_READ_ONLY="true", **env) as url:
        headers = {"Authorization": f"Bearer {MCP_TOKEN}"}
        async with Client[ClientTransport](StreamableHttpTransport(url, headers=headers)) as client:
            yield client


async def call(client: Connected, tool: str, **arguments: object) -> CallToolResult:
    return await client.call_tool(tool, arguments, raise_on_error=False)


async def output(client: Connected, tool: str, **arguments: object) -> Json:
    result = await call(client, tool, **arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
def scratch(stack: Stack) -> Iterator[tuple[str, list[int], int]]:
    """Two documents with one tag of their own, and a word only they hold."""
    word = f"plmcp83{uuid.uuid4().hex[:8]}"
    tag = num(obj(parse(stack.admin.post("/api/tags/", json={"name": word})))["id"])
    documents = [consume_text(stack.admin, f"{word} {n}") for n in (1, 2)]
    for document in documents:
        _ = stack.admin.patch(f"/api/documents/{document}/", json={"tags": [tag]})
    yield word, documents, tag
    delete_and_empty_trash(stack, documents)
    _ = stack.admin.delete(f"/api/tags/{tag}/")


async def test_the_next_number_is_one_more_than_the_highest(
    stack: Stack, transport: Transport, scratch: tuple[str, list[int], int]
) -> None:
    async with connected(stack, transport) as client:
        before = num((await output(client, "get_instance_overview"))["next_asn"])
        path = f"/api/documents/{scratch[1][0]}/"
        _ = stack.admin.patch(path, json={"archive_serial_number": before}).raise_for_status()
        after = (await output(client, "get_instance_overview"))["next_asn"]
    async with connected(stack, transport, token=stack.blind_token) as client:
        refused = (await output(client, "get_instance_overview"))["next_asn"]

    assert (after, refused) == (before + 1, None)


async def test_a_selection_is_counted_without_reading_its_rows(
    stack: Stack, transport: Transport, scratch: tuple[str, list[int], int]
) -> None:
    word, documents, tag = scratch
    async with connected(stack, transport) as client:
        found = await output(client, "read_documents", filters={"tags_all": [word]}, counts=True)

    counts = cast("dict[str, Json]", found["counts"])
    assert counts["tags"] == {
        "items": [{"id": tag, "name": word, "documents": 2}],
        "more": 0,
        "hidden": 0,
    }
    assert counts["correspondents"] == {"items": [], "more": 0, "hidden": 0}
    assert (found["documents"], sorted(cast("list[int]", found["selected_ids"]))) == (
        [],
        sorted(documents),
    )


async def test_a_hidden_tag_is_counted_and_not_named(
    stack: Stack, transport: Transport, scratch: tuple[str, list[int], int]
) -> None:
    word, [document, _], _ = scratch
    user = num(obj(obj(parse(stack.reader.get("/api/ui_settings/")))["user"])["id"])
    nobody: dict[str, list[int]] = {"users": [], "groups": []}
    grants = {"view": {**nobody, "users": [user]}, "change": nobody}
    path = f"/api/documents/{document}/"
    _ = stack.admin.patch(path, json={"set_permissions": grants}).raise_for_status()

    async with connected(stack, transport, token=stack.reader_token) as client:
        found = await output(client, "read_documents", document_ids=[document], counts=True)

    assert cast("dict[str, Json]", found["counts"])["tags"] == {
        "items": [],
        "more": 0,
        "hidden": 1,
    }
    assert word not in str(found)


async def test_words_are_suggested_from_the_documents_the_user_sees(
    stack: Stack, transport: Transport, scratch: tuple[str, list[int], int]
) -> None:
    rebuild_search_index(stack.admin)
    word = scratch[0]
    async with connected(stack, transport) as client:
        found = await output(client, "suggest_words", term=word[:-2].upper(), limit=50)
    async with connected(stack, transport, token=stack.reader_token) as client:
        hidden = await output(client, "suggest_words", term=word[:-2], limit=50)

    assert found["words"] == [word]
    assert hidden["words"] == []


async def test_a_storage_path_is_tried_and_nothing_moves(
    stack: Stack, transport: Transport, scratch: tuple[str, list[int], int]
) -> None:
    document = scratch[1][0]
    path = f"/api/documents/{document}/metadata/"
    before = obj(parse(stack.admin.get(path)))["media_filename"]
    async with connected(stack, transport) as client:
        stored = await output(
            client, "test_storage_path", document_id=document, storage_path="e2e-archive"
        )
        typed = await output(
            client, "test_storage_path", document_id=document, template="x/{{ title }}"
        )
        broken = await output(
            client, "test_storage_path", document_id=document, template="{{ nope }}"
        )
    async with connected(stack, transport, token=stack.reader_token) as client:
        hidden = await call(client, "test_storage_path", document_id=document, template="x")

    title = f"{scratch[0]} 1"
    assert str(stored["path"]).startswith("e2e/")
    assert str(stored["path"]).endswith(f"/{title}.txt")
    assert typed["path"] == f"x/{title}.txt"
    assert broken["path"] is None
    assert hidden.is_error
    assert obj(parse(stack.admin.get(path)))["media_filename"] == before


async def test_a_hidden_name_is_not_rendered(
    stack: Stack, transport: Transport, scratch: tuple[str, list[int], int]
) -> None:
    word, [document, _], _ = scratch
    user = num(obj(obj(parse(stack.reader.get("/api/ui_settings/")))["user"])["id"])
    nobody: dict[str, list[int]] = {"users": [], "groups": []}
    grants = {"view": {**nobody, "users": [user]}, "change": nobody}
    path = f"/api/documents/{document}/"
    body = {"set_permissions": grants, "owner": None}
    _ = stack.admin.patch(path, json=body).raise_for_status()

    async with connected(stack, transport, token=stack.reader_token) as client:
        refused = await call(
            client, "test_storage_path", document_id=document, template="{{ tag_list }}"
        )

    assert refused.is_error
    assert "may not see (tag)" in str(refused.content)
    assert word not in str(refused.content)


async def test_a_dot_and_a_path_without_end_are_no_outage(
    stack: Stack, transport: Transport, scratch: tuple[str, list[int], int]
) -> None:
    asked = {"document_id": scratch[1][0]}
    async with connected(stack, transport) as client:
        dot = await output(client, "test_storage_path", **asked, template="./")
        endless = await call(client, "test_storage_path", **asked, template="{{ 'a' * 1000000 }}")

    assert (dot["path"], "only a dot" in str(dot["note"])) == (None, True)
    assert endless.is_error
    assert "too long to read" in str(endless.content)
