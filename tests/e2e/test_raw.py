"""The raw toolset through the assembled server against the seeded Paperless 3.4.2 stack.

Reads the seeded tags, the OpenAPI-listed endpoints that hold secrets and files are refused
without a request, hostile paths never leave the API, and an answer over the cap is cut.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from harness import paperless_settings, server_settings

from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

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


SECRETS = ("e2e-secret-token-4711", "e2e-secret-bearer-4712", "e2e-secret-pdf-pass-4713")


@pytest.fixture
def workflow(stack: Stack) -> Iterator[Json]:
    """A disabled workflow that never fires, with a webhook and a password removal holding secrets.

    The workflows API leaves the triggers and actions behind when it deletes a workflow, so the
    fixture deletes those too, also when the test fails.
    """
    token, bearer, password = SECRETS
    unique = uuid.uuid4().hex
    made = stack.admin.post(
        "/api/workflows/",
        json={
            "name": f"e2e-raw-{unique}",
            "order": 99,
            "enabled": False,
            "triggers": [{"type": 2, "sources": [1], "filter_filename": f"never-{unique}.xyz"}],
            "actions": [
                {
                    "type": 4,
                    "webhook": {
                        "url": "https://example.invalid/hook",
                        "use_params": True,
                        "as_json": False,
                        "params": {"token": token},
                        "body": "",
                        "headers": {"Authorization": f"Bearer {bearer}"},
                        "include_document": False,
                    },
                },
                {"type": 5, "passwords": [password]},
            ],
        },
    )
    assert made.is_success, made.text
    body = cast("Json", made.json())
    try:
        yield body
    finally:
        _ = stack.admin.delete(f"/api/workflows/{body['id']}/")
        for kind, key in (("workflow_triggers", "triggers"), ("workflow_actions", "actions")):
            for row in cast("list[Json]", body[key]):
                _ = stack.admin.delete(f"/api/{kind}/{row['id']}/")


async def test_secrets_of_workflow_actions_are_not_returned(stack: Stack, workflow: Json) -> None:
    mine = f"/api/workflows/{workflow['id']}/"
    raw = stack.admin.get(mine).text
    assert all(secret in raw for secret in SECRETS)  # the seed really holds them

    for path in (mine, "/api/workflows/", "/api/workflow_actions/", "/api/search/"):
        output = await get(stack, path, {"query": "e2e-raw"} if path == "/api/search/" else None)
        shown = str(output["data"])
        assert not [secret for secret in SECRETS if secret in shown], path
        if path != "/api/search/":
            assert cast("int", output["redacted"]) > 0, path


async def test_page_links_carry_no_host(stack: Stack) -> None:
    output = await get(stack, "/api/tasks/", {"page_size": 1})

    data = cast("Json", output["data"])
    assert "127.0.0.1" not in str(output["data"])
    assert data["previous"] is None
    assert data["next"] == 2


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/api/profile/", "holds secrets"),
        ("/api/config/", "holds secrets"),
        ("/api/mail_accounts/", "holds secrets"),
        ("/api/documents/1/download/", "returns a file"),
        ("/api/documents/1/thumb/", "returns a file"),
        ("/api/documents/1/ai_suggestions/", "asks a language model"),
        ("https://evil.test/api/tags/", "path is refused"),
        ("/api/%2e%2e/admin/", "path is refused"),
        ("/api/tags/../profile/", "path is refused"),
    ],
)
async def test_denied_and_hostile_paths_are_refused(stack: Stack, path: str, message: str) -> None:
    async with connect(stack) as client:
        with pytest.raises(ToolError, match=message):
            _ = await client.call_tool(TOOL, {"path": path})


async def test_a_path_without_its_trailing_slash_is_a_redirect_that_is_not_followed(
    stack: Stack,
) -> None:
    async with connect(stack) as client:
        with pytest.raises(ToolError, match="redirected"):
            _ = await client.call_tool(TOOL, {"path": "/api/tags"})


async def test_an_unknown_document_is_not_found_and_an_unknown_endpoint_redirects(
    stack: Stack,
) -> None:
    async with connect(stack) as client:
        with pytest.raises(ToolError, match="does not exist"):
            _ = await client.call_tool(TOOL, {"path": "/api/documents/987654321/"})
        with pytest.raises(ToolError, match="redirected"):
            _ = await client.call_tool(TOOL, {"path": "/api/no_such_endpoint/"})
