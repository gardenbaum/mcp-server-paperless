"""Uploads over HTTP up to this server's own limit, on a real server (decision-40).

The MCP SDK refuses bodies over 4 MiB unless told otherwise, and FastMCP does not tell it: only a
real server and bodies above that show whether ``raise_sdk_limit`` still works after an update.
"""

import base64
import contextlib
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, override

import httpx2
import pytest
import uvicorn
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.http import StarletteWithLifespan, StreamableHTTPASGIApp
from harness import serve
from mcp import MCPError
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from mcp.server.transport_security import RequestBodyLimitMiddleware
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_server_paperless.app import (
    authenticator,
    body_limit,
    build_app,
    raise_sdk_limit,
    run_server,
)
from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.settings import HttpSettings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-40")]

MIB = 1024 * 1024
DEFAULT = 10 * MIB
TOKEN = "a-token-of-the-upload-tests"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


def http_settings(*, with_token: bool) -> HttpSettings:
    given = {"auth_tokens": TOKEN} if with_token else {"allow_unauthenticated_loopback": True}
    return HttpSettings.model_validate(given)


def make_app(max_upload_bytes: int, *, with_token: bool = True) -> Starlette:
    """The app with a fake Paperless behind it."""
    http = http_settings(with_token=with_token)
    fake = FakePaperless(seeded(), user="admin", allow_writes=True)
    server = serve(fake, auth=authenticator(http), max_upload_bytes=max_upload_bytes)
    return build_app(server, http, max_upload_bytes=max_upload_bytes)


def uvicorn_for(app: Starlette) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, ws="none")
    return uvicorn.Server(config)


@contextlib.contextmanager
def listening(app: Starlette) -> Generator[str]:
    """The URL of a uvicorn server on a free loopback port."""
    running = uvicorn_for(app)
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
    with listening(make_app(limit, with_token=with_token)) as url:
        fits = await upload(url, limit, with_token=with_token)
        over = await upload(url, limit + 1, with_token=with_token)

    assert (fits[0], '"state":"success"' in fits[1]) == (False, True), fits[1]
    assert over[0]
    assert f"larger than the limit of {limit // MIB} MiB" in over[1]


async def test_every_start_of_one_app_raises_the_limit() -> None:
    app = make_app(DEFAULT)
    for _ in range(2):
        with listening(app) as url:
            refused, answer = await upload(url, 5 * MIB, with_token=True)
        assert not refused, answer


async def test_the_limit_set_is_exactly_the_one_asked_for() -> None:
    app = make_app(DEFAULT)
    endpoint = fastmcp_endpoint(app)

    async with app.router.lifespan_context(app):
        assert limiter_of(endpoint).max_body_size == body_limit(DEFAULT)


@pytest.mark.parametrize("with_token", [True, False])
async def test_a_client_of_a_real_server_learns_that_the_request_is_too_large(
    *, with_token: bool
) -> None:
    with (
        listening(make_app(DEFAULT, with_token=with_token)) as url,
        pytest.raises(MCPError) as refused,
    ):
        _ = await upload(url, 12 * MIB, with_token=with_token)

    message = str(refused.value)
    assert f"at most {body_limit(DEFAULT)} bytes" in message
    assert "PAPERLESS_MCP_MAX_UPLOAD_BYTES" in message


async def test_a_real_server_refuses_an_oversized_body_without_a_token_by_its_length() -> None:
    with listening(make_app(DEFAULT, with_token=True)) as url:
        async with httpx2.AsyncClient() as client:
            large = await client.post(url, content=b"x" * body_limit(DEFAULT))
            over = await client.post(url, content=b"x" * (body_limit(DEFAULT) + 1))

    assert large.status_code == 401
    assert over.status_code == 413


def fastmcp_endpoint(app: Starlette) -> StreamableHTTPASGIApp:
    """FastMCP's own endpoint of an app with a token."""
    [guard] = [r.endpoint for r in app.routes if isinstance(r, Route) and r.path == "/mcp"]
    endpoint = getattr(guard, "app", None)
    assert isinstance(endpoint, StreamableHTTPASGIApp)
    return endpoint


def limiter_of(endpoint: StreamableHTTPASGIApp) -> RequestBodyLimitMiddleware:
    assert endpoint.session_manager is not None
    limiter = endpoint.session_manager.asgi_app
    assert isinstance(limiter, RequestBodyLimitMiddleware)
    return limiter


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


class Foreign:
    """Another object with an attribute of the same name, which nobody may read."""

    def __init__(self) -> None:
        self.asgi_app: SimpleNamespace = SimpleNamespace(max_body_size=4 * MIB)


def app_around(endpoint: object) -> Starlette:
    return Starlette(routes=[Route("/mcp", endpoint=endpoint)])  # pyright: ignore[reportArgumentType] -- an ASGI app


@pytest.mark.parametrize("guarded", [True, False])
@pytest.mark.parametrize("manager", [None, Other(), Moved(), Foreign()])
async def test_a_server_whose_limit_cannot_be_raised_does_not_start(
    manager: object, *, guarded: bool
) -> None:
    endpoint = StreamableHTTPASGIApp(manager)  # pyright: ignore[reportArgumentType] -- what an update could bring
    app = app_around(RequireAuthMiddleware(endpoint, []) if guarded else endpoint)
    raise_sdk_limit(app, 1)

    with pytest.raises(InvalidConfiguration, match="request body limit"):
        async with app.router.lifespan_context(app):
            pass


def without_the_limiter() -> Starlette:
    """FastMCP's real app and session manager, whose limit an update has taken away."""
    app = make_app(DEFAULT)
    endpoint = fastmcp_endpoint(app)
    start = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def bare(starting: Starlette) -> AsyncGenerator[None]:
        async with start(starting):
            del endpoint.session_manager.asgi_app  # pyright: ignore[reportOptionalMemberAccess] -- set at the start
            yield

    app.router.lifespan_context = bare
    raise_sdk_limit(app, 1)
    return app


async def test_the_refusal_of_a_real_start_comes_out_of_the_session_managers_task_group() -> None:
    app = without_the_limiter()

    with pytest.RaisesGroup(
        pytest.RaisesExc(InvalidConfiguration, match="request body limit"), flatten_subgroups=True
    ):
        async with app.router.lifespan_context(app):
            pass


def test_uvicorn_refuses_to_start_and_opens_no_socket(caplog: pytest.LogCaptureFixture) -> None:
    server = uvicorn_for(without_the_limiter())
    server.should_exit = True  # a server that did start stops at once

    with pytest.raises(SystemExit) as stopped:
        _ = run_server(server)

    assert stopped.value.code == 3
    assert not server.started
    assert not getattr(server, "servers", [])
    assert "cannot set the request body limit" in caplog.text


class Elsewhere(FastMCP):
    """A FastMCP whose app has no MCP endpoint of the known kind."""

    @override
    def http_app(self, *_given: object, **_options: object) -> StarletteWithLifespan:
        return StarletteWithLifespan()


def test_an_app_without_fastmcps_endpoint_is_not_built() -> None:
    http = http_settings(with_token=False)

    with pytest.raises(InvalidConfiguration, match="request body limit") as refused:
        _ = build_app(Elsewhere("x"), http, max_upload_bytes=1)

    assert "\n" not in refused.value.describe()  # the one line main_http prints
