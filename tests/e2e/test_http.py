"""Smoke test of the installed `mcp-server-paperless-http` script over HTTP against the E2E stack.

The real process, a real socket and FastMCP's HTTP client with a bearer token: what a remote
client does.
"""

import contextlib
import os
import re
import shutil
import socket
import subprocess
import time
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from guide_oracle import Seen, look

from e2e.helpers import EXPECTED_VERSION
from mcp_server_paperless.tools._shared import ANNOTATIONS, TOOL_TOOLSETS

if TYPE_CHECKING:
    from collections.abc import Generator

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-37")]

MCP_TOKEN = "test-token-not-a-secret"


def console_script() -> str:
    path = shutil.which("mcp-server-paperless-http")
    assert path, "the mcp-server-paperless-http console script is not installed (uv sync)"
    return path


@contextlib.contextmanager
def running(stack: Stack, **settings: str) -> Generator[str]:
    """The HTTP server as a process with `settings` in its environment; yields its MCP URL."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])  # pyright: ignore[reportAny] -- (host, port) tuple
    env = {
        **os.environ,
        "PAPERLESS_URL": stack.base_url,
        "PAPERLESS_TOKEN": stack.admin_token,
        "PAPERLESS_MCP_HTTP_PORT": str(port),
        "PAPERLESS_MCP_HTTP_AUTH_TOKENS": MCP_TOKEN,
        **settings,
    }
    process = subprocess.Popen(  # noqa: S603 -- our own console script, no shell
        [console_script()],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
                break
            assert process.poll() is None, "the HTTP server exited"
            time.sleep(0.2)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        _ = process.wait(timeout=30)


@pytest.fixture
def endpoint(stack: Stack) -> Generator[str]:
    with running(stack) as url:
        yield url


async def test_a_client_with_the_bearer_token_can_call_get_instance_overview(endpoint: str) -> None:
    transport = StreamableHttpTransport(endpoint, headers={"Authorization": f"Bearer {MCP_TOKEN}"})

    async with Client(transport) as client:
        result = await client.call_tool("get_instance_overview")

    output = result.structured_content
    assert output is not None
    assert (output["paperless_version"], output["api_version"]) == (EXPECTED_VERSION, 10)


def test_a_request_without_the_token_is_refused(endpoint: str) -> None:
    response = httpx2.post(endpoint, json={}, headers={"Accept": "application/json"})

    assert response.status_code == 401


async def over_http(url: str) -> Seen:
    transport = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
    async with Client(transport) as client:
        return await look(client)


async def test_the_setting_the_guide_names_switches_the_toolset_wanted_on_over_http(
    stack: Stack, endpoint: str
) -> None:
    seen = await over_http(endpoint)

    assert seen.guide is not None
    setting = re.search(r"PAPERLESS_MCP_TOOLSETS=(\S+)", seen.instructions)
    assert setting is not None, seen.instructions
    assert setting[1] == "core,ai,<name>"
    assert (
        "HTTP and image: environment, restart the process or recreate the container"
        in seen.instructions
    )
    wanted = "permissions"  # neither the first of the toolsets that are off nor the last
    [entry] = [
        e for e in cast("list[dict[str, object]]", seen.guide["off"]) if e["toolset"] == wanted
    ]

    with running(stack, PAPERLESS_MCP_TOOLSETS=setting[1].replace("<name>", wanted)) as restarted:
        after = await over_http(restarted)

    assert after.tools == seen.tools | set(cast("list[str]", entry["tools"]))


@pytest.mark.decision("decision-97")
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_a_mixed_selection_over_http(stack: Stack, mode: str) -> None:
    selection = "core:read,mail:read,workflows,tasks:read"
    with running(stack, PAPERLESS_MCP_TOOLSETS=selection) as url:
        transport = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
        async with Client(transport, mode=mode) as client:
            seen = await look(client)
            prompts = {prompt.name for prompt in await client.list_prompts()}
            refused = await client.call_tool(
                "delete_document", {"document_id": 1, "confirm": True}, raise_on_error=False
            )

    assert seen.tools == {
        tool
        for tool, toolset in TOOL_TOOLSETS.items()
        if toolset == "workflows"
        or (toolset in {"core", "mail", "tasks"} and ANNOTATIONS[tool].read_only_hint)
    }
    assert prompts == {"find_document"}
    assert f"PAPERLESS_MCP_TOOLSETS={selection},<name>" in seen.instructions
    assert refused.is_error
    assert "Unknown tool" in str(refused.content)


@pytest.mark.decision("decision-97")
async def test_server_wide_read_only_mode_wins_over_http(stack: Stack) -> None:
    with running(
        stack, PAPERLESS_MCP_TOOLSETS="core,mail:read", PAPERLESS_MCP_READ_ONLY="true"
    ) as url:
        seen = await over_http(url)

    assert seen.tools == {
        tool
        for tool, toolset in TOOL_TOOLSETS.items()
        if toolset in {"core", "mail"} and ANNOTATIONS[tool].read_only_hint
    }


@pytest.mark.decision("decision-97")
def test_a_wrong_read_only_selection_stops_the_http_server(stack: Stack) -> None:
    finished = subprocess.run(  # noqa: S603 -- our own console script, no shell
        [console_script()],
        env={
            **os.environ,
            "PAPERLESS_URL": stack.base_url,
            "PAPERLESS_TOKEN": stack.admin_token,
            "PAPERLESS_MCP_HTTP_AUTH_TOKENS": MCP_TOKEN,
            "PAPERLESS_MCP_TOOLSETS": "core,mail:ro",
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert finished.returncode == 1
    assert "Did you mean mail:read?" in finished.stderr
    assert "Traceback" not in finished.stderr
