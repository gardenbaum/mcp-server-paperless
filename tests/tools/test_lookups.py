"""suggest_words and test_storage_path through the in-memory client."""

from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakeDocument, FakePaperless, FakeTag, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.tools._shared import DOCUMENT_TEXT

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult

pytestmark = pytest.mark.anyio

type Json = dict[str, object]
TOOLS = {"suggest_words", "test_storage_path"}


async def called(tool: str, arguments: Json, user: str = "admin") -> CallToolResult:
    # No write is allowed: a lookup that writes fails its test.
    source = FakePaperless(seeded(), user=user)
    async with Client(serve(source, toolsets=["bulk"], read_only=True)) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


async def call(tool: str, **arguments: object) -> Json:
    result = await called(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def refusal(tool: str, arguments: Json, user: str = "admin") -> str:
    result = await called(tool, arguments, user)
    assert result.is_error
    return str(result.content)


async def test_both_are_read_only_tools_outside_the_default_profile() -> None:
    async with Client(serve(FakePaperless(seeded(), user="admin"))) as client:
        default = {tool.name for tool in await client.list_tools()}
    async with Client(
        serve(FakePaperless(seeded(), user="admin"), toolsets=["bulk"], read_only=True)
    ) as client:
        offered = {tool.name: tool for tool in await client.list_tools()}

    assert not default & set(offered)
    assert set(offered) >= TOOLS
    for name in TOOLS:
        assert DOCUMENT_TEXT in (offered[name].description or "").replace("\n", " ")


async def test_words_are_suggested() -> None:
    output = await call("suggest_words", term="invo")

    assert "invoice" in str(output["words"])
    assert output["too_long"] == 0


@pytest.mark.parametrize("limit", [0, 51])
async def test_one_to_fifty_words_may_be_asked_for(limit: int) -> None:
    assert "limit" in await refusal("suggest_words", {"term": "a", "limit": limit})


async def test_a_stored_path_and_a_template_are_tried() -> None:
    by_name = await call("test_storage_path", document_id=1, storage_path="e2e-archive")
    template = await call("test_storage_path", document_id=1, template="x/{{ title }}")
    broken = await call("test_storage_path", document_id=1, template="{{ nope }}")

    assert by_name["storage_path"] == {"id": 1, "name": "e2e-archive"}
    assert template == {
        "document_id": 1,
        "storage_path": None,
        "path": "x/e2e-public-invoice.pdf",
        "note": None,
    }
    assert (broken["path"], "cannot be rendered" in str(broken["note"])) == (None, True)


async def test_neither_and_both_and_hidden_documents_are_refused() -> None:
    both: Json = {"document_id": 1, "storage_path": 1, "template": "x"}
    assert "exactly one" in await refusal("test_storage_path", {"document_id": 1})
    assert "exactly one" in await refusal("test_storage_path", both)
    assert "No document 2" in await refusal(
        "test_storage_path", {"document_id": 2, "template": "x"}, "e2e-reader"
    )


async def test_a_template_may_have_two_thousand_characters() -> None:
    _ = await call("test_storage_path", document_id=1, template="a" * 2000)
    assert "2000" in await refusal("test_storage_path", {"document_id": 1, "template": "a" * 2001})


@pytest.mark.decision("decision-100")
async def test_hidden_names_are_not_shown_by_a_template() -> None:
    state = seeded()
    state.tags.append(FakeTag(id=70, name="secret-tag", owner=2))
    state.documents.append(FakeDocument(id=10, title="mine", owner=3, tags=[70]))
    async with Client(
        serve(FakePaperless(state, user="e2e-reader"), toolsets=["bulk"], read_only=True)
    ) as client:
        result = await client.call_tool(
            "test_storage_path", {"document_id": 10, "template": "x"}, raise_on_error=False
        )

    assert result.is_error
    assert "may not see (tag)" in str(result.content)
    assert "secret" not in str(result.content)
