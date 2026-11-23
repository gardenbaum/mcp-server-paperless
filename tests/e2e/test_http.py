"""Smoke test of `mcp-server-paperless-http` over Streamable HTTP against Paperless 3.4.2.

The real process, a real socket and FastMCP's HTTP client with a bearer token: what a remote
client does.
"""

import contextlib
import os
import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from e2e.helpers import EXPECTED_VERSION

if TYPE_CHECKING:
    from collections.abc import Generator

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

MCP_TOKEN = "test-token-not-a-secret"


@pytest.fixture
def endpoint(stack: Stack) -> Generator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])  # pyright: ignore[reportAny] -- (host, port) tuple
    env = {
        **os.environ,
        "PAPERLESS_URL": stack.base_url,
        "PAPERLESS_TOKEN": stack.admin_token,
        "PAPERLESS_MCP_HTTP_PORT": str(port),
        "PAPERLESS_MCP_HTTP_AUTH_TOKENS": MCP_TOKEN,
    }
    process = subprocess.Popen(
        [sys.executable, "-c", "from mcp_server_paperless.cli import main_http; main_http()"],
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
