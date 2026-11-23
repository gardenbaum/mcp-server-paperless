"""Uploads over HTTP up to this server's own limit, on a real server (decision-40).

The MCP SDK refuses bodies over 4 MiB unless told otherwise, and FastMCP does not tell it: only a
real server and bodies above that show whether ``raise_sdk_limit`` still works after an update.
"""

import base64
import contextlib
import threading
import time
from typing import TYPE_CHECKING

import httpx2
import pytest
import uvicorn
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.http import StreamableHTTPASGIApp
from harness import serve
from mcp import MCPError
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from mcp.server.transport_security import RequestBodyLimitMiddleware
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_server_paperless.app import authenticator, body_limit, build_app, raise_sdk_limit
from mcp_server_paperless.settings import HttpSettings

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-40")]

MIB = 1024 * 1024
DEFAULT = 10 * MIB
TOKEN = "a-token-of-the-upload-tests"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


def http_settings(*, with_token: bool) -> HttpSettings:
    given = {"auth_tokens": TOKEN} if with_token else {"allow_unauthenticated_loopback": True}
    return HttpSettings.model_validate(given)


@contextlib.contextmanager
def listening(max_upload_bytes: int, *, with_token: bool) -> Generator[str]:
    """The URL of a uvicorn server on a free loopback port, with a fake Paperless behind it."""
    http = http_settings(with_token=with_token)
    fake = FakePaperless(seeded(), user="admin", allow_writes=True)
    server = serve(fake, auth=authenticator(http), max_upload_bytes=max_upload_bytes)
    app = build_app(server, http, max_upload_bytes=max_upload_bytes)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, ws="none")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not running.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert running.started
        port = int(running.servers[0].sockets[0].getsockname()[1])  # pyright: ignore[reportAny] -- (host, port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        running.should_exit = True
        thread.join(30)


async def upload(url: str, size: int, *, with_token: bool) -> tuple[bool, str]:
    """Whether the tool refused a file of ``size`` bytes, and what it answered."""
    content = base64.b64encode(b"%PDF-1.4\n" + b"x" * (size - 9)).decode()
    transport = StreamableHttpTransport(url, headers=BEARER if with_token else {})
    async with Client(transport) as client:
        result = await client.call_tool(
            "upload_document",
            {"content_base64": content, "filename": "a.pdf"},
            raise_on_error=False,
        )
    return result.is_error, str(result.content)


@pytest.mark.parametrize("with_token", [True, False])
@pytest.mark.parametrize("limit", [DEFAULT, 20 * MIB])
async def test_a_file_of_the_maximum_size_reaches_the_tool(limit: int, *, with_token: bool) -> None:
    with listening(limit, with_token=with_token) as url:
        fits = await upload(url, limit, with_token=with_token)
        over = await upload(url, limit + 1, with_token=with_token)

    assert (fits[0], '"state":"success"' in fits[1]) == (False, True), fits[1]
    assert over[0]
    assert f"larger than the limit of {limit // MIB} MiB" in over[1]


@pytest.mark.parametrize("with_token", [True, False])
async def test_a_client_of_a_real_server_learns_that_the_request_is_too_large(
    *, with_token: bool
) -> None:
    with listening(DEFAULT, with_token=with_token) as url, pytest.raises(MCPError) as refused:
        _ = await upload(url, 12 * MIB, with_token=with_token)

    assert f"at most {body_limit(DEFAULT)} bytes" in str(refused.value)
    assert "PAPERLESS_MCP_MAX_UPLOAD_BYTES" in str(refused.value)


async def test_a_real_server_refuses_an_oversized_body_without_a_token_by_its_length() -> None:
    with listening(DEFAULT, with_token=True) as url:
        async with httpx2.AsyncClient() as client:
            large = await client.post(url, content=b"x" * (5 * MIB))
            over = await client.post(url, content=b"x" * (body_limit(DEFAULT) + 1))

    assert large.status_code == 401
    assert over.status_code == 413


class Other:
    """A session manager that no longer has the SDK's limit where it used to be."""

    asgi_app: object = None


class Renamed(RequestBodyLimitMiddleware):
    """The SDK's limit under another name."""

    def __init__(self) -> None:
        super().__init__(Starlette(), 4 * MIB)
        del self.max_body_size


class Moved:
    asgi_app: Renamed = Renamed()


def app_around(endpoint: object) -> Starlette:
    return Starlette(routes=[Route("/mcp", endpoint=endpoint)])  # pyright: ignore[reportArgumentType] -- an ASGI app


@pytest.mark.parametrize("guarded", [True, False])
@pytest.mark.parametrize("manager", [None, Other(), Moved()])
async def test_a_server_whose_limit_cannot_be_raised_does_not_start(
    manager: object, *, guarded: bool
) -> None:
    endpoint = StreamableHTTPASGIApp(manager)  # pyright: ignore[reportArgumentType] -- what an update could bring
    app = app_around(RequireAuthMiddleware(endpoint, []) if guarded else endpoint)
    raise_sdk_limit(app, 1)

    with pytest.raises(RuntimeError, match="request body limit"):
        async with app.router.lifespan_context(app):
            pass


def test_an_app_without_fastmcps_endpoint_is_not_built() -> None:
    with pytest.raises(RuntimeError, match="request body limit"):
        raise_sdk_limit(Starlette(), 1)
