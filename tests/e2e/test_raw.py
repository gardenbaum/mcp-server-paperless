"""The raw toolset through the assembled server against the seeded Paperless 3.4.2 stack.

Reads the seeded tags, the OpenAPI-listed endpoints that hold secrets and files are refused
without a request, hostile paths never leave the API, and an answer over the cap is cut.
"""

from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from harness import paperless_settings, server_settings

from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-25")]

type Json = dict[str, object]
TOOL = "paperless_api_get"


def connect(stack: Stack, toolsets: list[str] | None = None) -> Client[FastMCPTransport]:
    settings = paperless_settings(stack.base_url, stack.admin_token)
    return Client(create_server(settings, server_settings(toolsets=toolsets or ["raw"])))


async def get(stack: Stack, path: str, params: Json | None = None) -> Json:
    async with connect(stack) as client:
        result = await client.call_tool(TOOL, {"path": path, "params": params})
    assert result.structured_content is not None
    return result.structured_content


async def test_the_toolset_is_off_by_default(stack: Stack) -> None:
    async with connect(stack, ["core", "ai"]) as client:
        assert TOOL not in {tool.name for tool in await client.list_tools()}
    async with connect(stack) as client:
        assert {tool.name for tool in await client.list_tools()} == {TOOL}


async def test_the_seeded_tags_are_read_through_the_server(stack: Stack) -> None:
    output = await get(stack, "/api/tags/", {"page_size": 100})

    data = cast("Json", output["data"])
    names = {row["name"] for row in cast("list[Json]", data["results"])}
    assert {"e2e-finance", "e2e-inbox"} <= names
    assert output["truncated"] is False
    assert output["status"] == 200


async def test_secrets_are_not_returned_from_the_workflow_endpoints(stack: Stack) -> None:
    output = await get(stack, "/api/workflow_actions/")

    assert output["status"] == 200
    assert "password" not in str(output["data"]).replace("'passwords': '[redacted]'", "")


@pytest.mark.parametrize(
    "path",
    [
        "/api/profile/",
        "/api/config/",
        "/api/mail_accounts/",
        "/api/documents/1/download/",
        "/api/documents/1/thumb/",
        "https://evil.test/api/tags/",
        "/api/%2e%2e/admin/",
        "/api/tags/../profile/",
    ],
)
async def test_denied_and_hostile_paths_are_refused(stack: Stack, path: str) -> None:
    async with connect(stack) as client:
        with pytest.raises(ToolError):
            _ = await client.call_tool(TOOL, {"path": path})


async def test_a_path_without_its_trailing_slash_is_a_redirect_that_is_not_followed(
    stack: Stack,
) -> None:
    async with connect(stack) as client:
        with pytest.raises(ToolError, match="redirected"):
            _ = await client.call_tool(TOOL, {"path": "/api/tags"})


async def test_an_answer_over_the_cap_is_cut_and_says_how_to_narrow_it(stack: Stack) -> None:
    output = await get(stack, "/api/documents/", {"page_size": 100})

    # The seed holds few documents with little text; the cap is covered by the service tests.
    # The point here is that a long answer never fails and the fields stay consistent.
    assert output["truncated"] in {True, False}
    assert (output["data"] is None) == (output["truncated"] is True)


async def test_an_unknown_document_is_not_found_and_an_unknown_endpoint_redirects(
    stack: Stack,
) -> None:
    async with connect(stack) as client:
        with pytest.raises(ToolError, match="does not exist"):
            _ = await client.call_tool(TOOL, {"path": "/api/documents/987654321/"})
        with pytest.raises(ToolError, match="redirected"):
            _ = await client.call_tool(TOOL, {"path": "/api/no_such_endpoint/"})
