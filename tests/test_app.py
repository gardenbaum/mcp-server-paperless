"""The HTTP transport is secure by default (decision-27): startup rules, auth, hosts, limits."""

import contextlib
import json
import logging
import re
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import fastmcp
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from harness import admin, serve
from pydantic import SecretStr

from mcp_server_paperless.app import (
    BodyLimit,
    BodyTooLargeError,
    LoopbackOnly,
    StaticBearerVerifier,
    authenticator,
    body_limit,
    build_app,
    check_startup,
    is_loopback,
)
from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.observability import JsonFormatter, RequestContext
from mcp_server_paperless.settings import HttpSettings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterator

    from starlette.types import Message, Receive, Scope, Send

TOKEN = "test-token-not-a-secret"

pytestmark = pytest.mark.decision("decision-27")

# What the injected resolver says for host names; anything else does not resolve.
NAMES: dict[str, list[str]] = {
    "localhost": ["127.0.0.1", "::1"],
    "mixed.test": ["127.0.0.1", "10.0.0.5"],
    "elsewhere.test": ["203.0.113.7"],
    "short-loopback": ["127.0.0.1"],
}


def resolve(host: str) -> list[str]:
    if host not in NAMES:
        message = f"cannot resolve {host}"
        raise OSError(message)
    return NAMES[host]


def field(record: logging.LogRecord, name: str) -> object:
    """An attribute that came in through `extra`."""
    return cast("dict[str, object]", vars(record))[name]


def settings(**given: object) -> HttpSettings:
    return HttpSettings.model_validate(given)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.2",
        "127.255.255.254",
        "::1",
        "[::1]",
        "::ffff:127.0.0.1",
        "::ffff:7f00:1",
        "localhost",
        "LOCALHOST",
        "short-loopback",
    ],
)
def test_loopback_addresses_and_names_are_loopback(host: str) -> None:
    assert is_loopback(host, resolve)


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",  # noqa: S104 -- a value under test
        "::",
        "[::]",
        "",
        "10.0.0.5",
        "192.168.1.1",
        "::ffff:10.0.0.5",
        "::ffff:0.0.0.0",
        "fe80::1%eth0",
        "mixed.test",
        "elsewhere.test",
        "unknown.test",
        "127.0.0.1.evil.test",
        "localhost.evil.test",
    ],
)
def test_everything_else_is_remote(host: str) -> None:
    assert not is_loopback(host, resolve)


def refusal(http: HttpSettings) -> str:
    with pytest.raises(InvalidConfiguration) as caught:
        check_startup(http, resolve)
    return caught.value.describe()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "0.0.0.0", "10.0.0.5"])  # noqa: S104 -- a value under test
def test_without_tokens_it_refuses_to_start_unless_loopback_is_allowed_explicitly(
    host: str,
) -> None:
    message = refusal(settings(host=host))

    assert "PAPERLESS_MCP_HTTP_AUTH_TOKENS" in message
    assert "authentication" in message


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"])
def test_an_explicit_flag_allows_loopback_without_tokens(host: str) -> None:
    check_startup(settings(host=host, allow_unauthenticated_loopback=True), resolve)


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "10.0.0.5", "mixed.test", "elsewhere.test", "unknown.test", ""],  # noqa: S104 -- a value under test
)
def test_the_flag_never_opens_a_remote_bind(host: str) -> None:
    message = refusal(settings(host=host, allow_unauthenticated_loopback=True))

    assert "loopback" in message
    assert "PAPERLESS_MCP_HTTP_AUTH_TOKENS" in message


def test_tokens_alone_are_enough_on_loopback() -> None:
    check_startup(settings(auth_tokens=TOKEN), resolve)


def test_a_remote_bind_with_tokens_needs_the_hosts_clients_use() -> None:
    message = refusal(settings(host="0.0.0.0", auth_tokens=TOKEN))  # noqa: S104 -- a value under test

    assert "PAPERLESS_MCP_HTTP_ALLOWED_HOSTS" in message


def test_a_remote_bind_with_tokens_and_hosts_starts() -> None:
    check_startup(
        settings(host="0.0.0.0", auth_tokens=TOKEN, allowed_hosts="mcp.example.com"),  # noqa: S104 -- a value under test
        resolve,
    )


# --- the running app ---------------------------------------------------------------------------

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
MAX_UPLOAD = 10 * 1024 * 1024
BEARER = {"Authorization": f"Bearer {TOKEN}"}


@contextlib.asynccontextmanager
async def running(
    http: HttpSettings, *, base_url: str = "http://localhost", max_upload_bytes: int | None = None
) -> AsyncGenerator[httpx2.AsyncClient]:
    """The app of the harness server behind an in-process HTTP client (lifespan included)."""
    server = serve(
        admin(), toolsets=["all"], max_upload_bytes=max_upload_bytes, auth=authenticator(http)
    )
    app = build_app(server, http, max_upload_bytes=max_upload_bytes or MAX_UPLOAD)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=base_url) as client,
    ):
        yield client


async def post(client: httpx2.AsyncClient, headers: dict[str, str]) -> httpx2.Response:
    return await client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **headers})


@pytest.mark.anyio
async def test_a_request_without_a_token_gets_401_with_a_bearer_challenge() -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await post(client, {})

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "authorization",
    [
        f"Bearer {TOKEN}x",
        f"Bearer {TOKEN[:-1]}",
        "Bearer ",
        "Bearer",
        f"Basic {TOKEN}",
        f"bearer-{TOKEN}",
        TOKEN,
    ],
)
async def test_a_wrong_or_malformed_credential_gets_401(authorization: str) -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await post(client, {"Authorization": authorization})

    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["POST", "GET", "DELETE", "HEAD"])
async def test_every_method_the_endpoint_serves_needs_a_token(method: str) -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await client.request(method, "/mcp", headers=MCP_HEADERS)

    assert response.status_code == 401


@pytest.mark.anyio
async def test_a_token_in_the_query_string_is_not_a_credential() -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await client.post(
            f"/mcp?token={TOKEN}&access_token={TOKEN}", json=INITIALIZE, headers=MCP_HEADERS
        )

    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("token", [TOKEN, f"{TOKEN}-second"])
async def test_any_configured_token_opens_the_endpoint(token: str) -> None:
    async with running(settings(auth_tokens=f"{TOKEN},{TOKEN}-second")) as client:
        response = await post(client, {"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert '"name":"mcp-server-paperless"' in response.text


@pytest.mark.anyio
async def test_without_tokens_the_flagged_loopback_app_is_open() -> None:
    async with running(
        settings(allow_unauthenticated_loopback=True), base_url="http://127.0.0.1"
    ) as client:
        response = await post(client, {})

    assert response.status_code == 200


@pytest.mark.anyio
async def test_the_verifier_returns_a_label_never_the_token() -> None:
    verifier = StaticBearerVerifier([SecretStr(TOKEN), SecretStr(f"{TOKEN}-second")])

    first = await verifier.verify_token(TOKEN)
    second = await verifier.verify_token(f"{TOKEN}-second")

    assert first is not None
    assert second is not None
    assert (first.client_id, second.client_id) == ("static-token-0", "static-token-1")
    assert TOKEN not in repr(first)
    assert await verifier.verify_token("") is None
    assert await verifier.verify_token(f"{TOKEN} ") is None


def test_a_server_without_the_authentication_the_settings_ask_for_is_refused() -> None:
    open_server = serve(admin())

    with pytest.raises(InvalidConfiguration, match="authentication"):
        _ = build_app(open_server, settings(auth_tokens=TOKEN), max_upload_bytes=MAX_UPLOAD)
    with pytest.raises(InvalidConfiguration, match="authentication"):
        _ = build_app(
            serve(admin(), auth=authenticator(settings(auth_tokens=TOKEN))),
            settings(allow_unauthenticated_loopback=True),
            max_upload_bytes=MAX_UPLOAD,
        )


# Hosts the app answers to by default: the loopback names (DNS-rebinding protection).
@pytest.mark.anyio
@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "10.0.0.5", ""])
async def test_a_foreign_host_header_is_refused_before_authentication(host: str) -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await post(client, {**BEARER, "Host": host})

    assert response.status_code == 421


@pytest.mark.anyio
@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
async def test_the_loopback_host_names_are_always_allowed(host: str) -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await post(client, {**BEARER, "Host": host})

    assert response.status_code == 200


@pytest.mark.anyio
async def test_configured_hosts_are_allowed_and_others_are_not() -> None:
    http = settings(
        host="0.0.0.0",  # noqa: S104 -- a value under test
        auth_tokens=TOKEN,
        allowed_hosts="mcp.example.com",
    )
    async with running(http, base_url="http://mcp.example.com") as client:
        allowed = await post(client, BEARER)
        other = await post(client, {**BEARER, "Host": "evil.example"})
        suffix = await post(client, {**BEARER, "Host": "mcp.example.com.evil.example"})
        prefix = await post(client, {**BEARER, "Host": "evilmcp.example.com"})

    assert [r.status_code for r in (allowed, other, suffix, prefix)] == [200, 421, 421, 421]


@pytest.mark.anyio
async def test_a_browser_origin_that_is_not_the_host_is_refused() -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        foreign = await post(client, {**BEARER, "Origin": "https://evil.example"})
        same = await post(client, {**BEARER, "Origin": "http://localhost"})

    assert (foreign.status_code, same.status_code) == (403, 200)


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/", "/health", "/mcp/", "/.well-known/oauth-protected-resource"])
@pytest.mark.parametrize("headers", [{}, BEARER])
async def test_the_endpoint_is_the_only_route(path: str, headers: dict[str, str]) -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await client.get(path, headers=headers)

    assert response.status_code == 404  # no redirect either: it would drop the port and scheme
    assert response.text == "Not Found"


@pytest.mark.anyio
async def test_the_endpoint_path_is_configurable() -> None:
    server = serve(admin(), auth=authenticator(settings(auth_tokens=TOKEN, path="/api/mcp")))
    app = build_app(
        server, settings(auth_tokens=TOKEN, path="/api/mcp"), max_upload_bytes=MAX_UPLOAD
    )
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
        ) as c,
    ):
        moved = await c.post("/api/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **BEARER})
        old = await c.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **BEARER})

    assert (moved.status_code, old.status_code) == (200, 404)


@pytest.mark.anyio
async def test_every_response_carries_a_request_id_even_a_refusal() -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        refused = await post(client, {})
        served = await post(client, BEARER)
        wrong_host = await post(client, {**BEARER, "Host": "evil.example"})

    ids = [r.headers["x-request-id"] for r in (refused, served, wrong_host)]
    assert all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)
    assert len(set(ids)) == 3


@pytest.mark.anyio
async def test_a_well_formed_request_id_of_the_caller_is_kept() -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await post(client, {**BEARER, "X-Request-ID": "trace-1.A_b"})

    assert response.headers["x-request-id"] == "trace-1.A_b"


@pytest.mark.anyio
@pytest.mark.parametrize("given", ["a" * 65, "has space", "tab\there", "x;y", ""])
async def test_a_malformed_request_id_is_replaced(given: str) -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await post(client, {**BEARER, "X-Request-ID": given})

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


@pytest.mark.anyio
async def test_each_request_is_logged_without_credentials_or_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    async with running(settings(auth_tokens=TOKEN)) as client:
        _ = await post(client, {"Authorization": "Bearer wrong-token-not-a-secret"})
        _ = await client.post(
            "/mcp?secret=query-text", json=INITIALIZE, headers={**MCP_HEADERS, **BEARER}
        )

    records = [r for r in caplog.records if r.getMessage() == "http request"]
    assert [(field(r, "method"), field(r, "path"), field(r, "status")) for r in records] == [
        ("POST", "/mcp", 401),
        ("POST", "/mcp", 200),
    ]
    assert all(re.fullmatch(r"[0-9a-f]{32}", str(field(r, "request_id"))) for r in records)
    # The test client's own httpx2 records are not the server's.
    everything = "\n".join(
        f"{r.getMessage()} {vars(r)}" for r in caplog.records if r.name != "httpx2"
    )
    assert TOKEN not in everything
    assert "wrong-token-not-a-secret" not in everything
    assert "query-text" not in everything


@pytest.mark.anyio
async def test_a_tool_call_is_logged_with_the_request_id_of_its_http_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    caplog.handler.setFormatter(JsonFormatter())
    http = settings(auth_tokens=TOKEN)
    server = serve(admin(), auth=authenticator(http))
    app = build_app(server, http, max_upload_bytes=MAX_UPLOAD)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        *,
        follow_redirects: bool = True,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=follow_redirects,
        )

    async with app.router.lifespan_context(app):
        transport = StreamableHttpTransport(
            "http://localhost/mcp",
            headers={**BEARER, "X-Request-ID": "trace-for-the-call"},
            httpx_client_factory=factory,
        )
        async with Client(transport) as client:
            _ = await client.call_tool("get_instance_overview")

    lines = [
        cast("dict[str, object]", json.loads(line))
        for line in caplog.text.splitlines()
        if line.startswith("{")
    ]
    calls = [line for line in lines if line["message"] == "tool call"]
    assert [line["request_id"] for line in calls] == ["trace-for-the-call"]


# One byte of upload allows 4 base64 characters plus 64 KiB for the JSON-RPC envelope.
SMALL_LIMIT = 4 + 64 * 1024


def test_the_body_limit_is_the_base64_size_of_the_upload_limit_plus_the_envelope() -> None:
    assert body_limit(1) == SMALL_LIMIT
    assert body_limit(3) == SMALL_LIMIT
    assert body_limit(4) == 8 + 64 * 1024
    assert body_limit(10 * 1024 * 1024) == 13981016 + 64 * 1024


@pytest.mark.anyio
async def test_a_body_larger_than_the_limit_is_refused_by_its_declared_length() -> None:
    async with running(settings(auth_tokens=TOKEN), max_upload_bytes=1) as client:
        response = await client.post(
            "/mcp", content=b"x" * (SMALL_LIMIT + 1), headers={**MCP_HEADERS, **BEARER}
        )
        fits = await post(client, BEARER)

    assert response.status_code == 413
    assert fits.status_code == 200


@pytest.mark.anyio
async def test_a_streamed_body_is_cut_off_at_the_limit() -> None:
    pulled = 0
    chunk = b"x" * 8192

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal pulled
        for _ in range(1000):
            pulled += 1
            yield chunk

    async with running(settings(auth_tokens=TOKEN), max_upload_bytes=1) as client:
        response = await client.post("/mcp", content=chunks(), headers={**MCP_HEADERS, **BEARER})

    assert response.status_code == 413
    assert pulled * len(chunk) <= SMALL_LIMIT + 2 * len(chunk)


@pytest.mark.anyio
async def test_an_unauthenticated_oversized_body_is_never_read() -> None:
    pulled = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal pulled
        for _ in range(1000):
            pulled += 1
            yield b"x" * 8192

    async with running(settings(auth_tokens=TOKEN), max_upload_bytes=1) as client:
        response = await client.post("/mcp", content=chunks(), headers=MCP_HEADERS)

    assert response.status_code == 401
    assert pulled <= 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "base_url",
    ["http://10.0.0.5", "http://[2001:db8::1]", "http://example.org", "http://localhost"],
)
async def test_the_unauthenticated_app_answers_only_on_a_loopback_address(base_url: str) -> None:
    http = settings(allow_unauthenticated_loopback=True, allowed_hosts=urlsplit(base_url).hostname)
    async with running(http, base_url=base_url) as client:
        response = await post(client, {})

    assert response.status_code == 403
    assert "loopback" in response.text  # a name is never classified per request: no DNS lookup


@pytest.mark.anyio
@pytest.mark.parametrize(
    "base_url", ["http://127.0.0.1:9", "http://[::1]:9", "http://[::ffff:127.0.0.1]"]
)
async def test_the_unauthenticated_app_answers_on_any_loopback_address(base_url: str) -> None:
    async with running(settings(allow_unauthenticated_loopback=True), base_url=base_url) as client:
        response = await post(client, {})

    assert response.status_code == 200


@pytest.mark.anyio
async def test_an_authenticated_app_does_not_care_about_the_address_it_is_reached_on() -> None:
    http = settings(auth_tokens=TOKEN, host="0.0.0.0", allowed_hosts="10.0.0.5")  # noqa: S104 -- a value under test
    async with running(http, base_url="http://10.0.0.5") as client:
        response = await post(client, BEARER)

    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost"])
async def test_no_cross_origin_access_is_ever_granted(origin: str) -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await client.options(
            "/mcp",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST", **BEARER},
        )
        served = await post(client, {**BEARER, "Origin": origin})

    for answer in (response, served):
        names = [str(name).lower() for name in answer.headers.keys()]  # noqa: SIM118 -- typed keys
        assert not [name for name in names if name.startswith("access-control-")]


def test_an_address_the_resolver_cannot_make_sense_of_is_remote() -> None:
    assert not is_loopback("odd.test", lambda _host: ["not-an-address"])
    assert not is_loopback("odd.test", lambda _host: [])


class Recorder:
    """An ASGI app that records the scope types it served and, if asked, reads the body."""

    def __init__(self, *, reads: bool = False, responds_first: bool = False) -> None:
        self.scopes: list[str] = []
        self.reads: bool = reads
        self.responds_first: bool = responds_first

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(cast("str", scope["type"]))
        if self.responds_first:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        while self.reads:
            if not (await receive()).get("more_body"):
                break


async def idle() -> Message:
    return {"type": "http.request", "body": b"x" * 10, "more_body": True}


async def discard(_message: Message) -> None:
    return None


@pytest.mark.anyio
@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_the_middleware_leave_other_connection_kinds_alone(scope_type: str) -> None:
    for inner, wrapped in [
        (first := Recorder(), BodyLimit(first, limit=1)),
        (second := Recorder(), RequestContext(second)),
        (third := Recorder(), LoopbackOnly(third)),
    ]:
        await wrapped({"type": scope_type}, idle, discard)
        assert inner.scopes == [scope_type]


@pytest.mark.anyio
async def test_a_body_that_passes_the_limit_after_the_response_began_is_not_masked() -> None:
    limited = BodyLimit(Recorder(reads=True, responds_first=True), limit=15)
    scope: Scope = {"type": "http", "path": "/", "headers": []}

    with pytest.raises(BodyTooLargeError):
        await limited(scope, idle, discard)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "header", ["Forwarded", "X-Forwarded-For", "X-Real-IP", "x-forwarded-host"]
)
async def test_the_unauthenticated_app_refuses_requests_that_came_through_a_proxy(
    header: str,
) -> None:
    http = settings(allow_unauthenticated_loopback=True)
    async with running(http, base_url="http://127.0.0.1") as client:
        proxied = await post(client, {header: "203.0.113.7"})
        direct = await post(client, {})

    assert (proxied.status_code, direct.status_code) == (403, 200)
    assert "proxy" in proxied.text


@pytest.mark.anyio
@pytest.mark.parametrize("header", ["Forwarded", "X-Forwarded-For", "X-Real-IP"])
async def test_the_authenticated_app_ignores_proxy_headers(header: str) -> None:
    async with running(settings(auth_tokens=TOKEN)) as client:
        response = await post(client, {**BEARER, header: "203.0.113.7"})

    assert response.status_code == 200


@pytest.fixture
def fastmcp_loosened() -> Iterator[None]:
    """What FASTMCP_* environment variables would set, applied to FastMCP's settings."""
    live = fastmcp.settings
    saved = (
        live.http_allowed_origins,
        live.debug,
        live.stateless_http,
        live.http_session_idle_timeout,
    )
    fastmcp.settings.http_allowed_origins = ["*"]
    fastmcp.settings.debug = True
    fastmcp.settings.stateless_http = True
    fastmcp.settings.http_session_idle_timeout = None
    try:
        yield
    finally:
        (
            live.http_allowed_origins,
            live.debug,
            live.stateless_http,
            live.http_session_idle_timeout,
        ) = saved


@pytest.mark.anyio
@pytest.mark.usefixtures("fastmcp_loosened")
async def test_fastmcp_environment_settings_cannot_loosen_the_app() -> None:
    http = settings(auth_tokens=TOKEN)
    server = serve(admin(), auth=authenticator(http))
    app = build_app(server, http, max_upload_bytes=MAX_UPLOAD)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
        ) as c,
    ):
        foreign = await c.post(
            "/mcp",
            json=INITIALIZE,
            headers={**MCP_HEADERS, **BEARER, "Origin": "https://evil.example"},
        )
        null = await c.post(
            "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **BEARER, "Origin": "null"}
        )
        stream = await c.get("/mcp", headers=MCP_HEADERS)  # a GET route exists only if stateful

    assert [foreign.status_code, null.status_code, stream.status_code] == [403, 403, 401]
    assert app.debug is False


class Exploding:
    """An ASGI app that fails, before it answers or, if asked, after it began to."""

    def __init__(self, *, after_start: bool = False) -> None:
        self.after_start: bool = after_start

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        _ = (scope, receive)
        if self.after_start:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        message = "secret internal detail"
        raise RuntimeError(message)


@pytest.mark.anyio
async def test_an_error_after_the_answer_began_is_not_swallowed() -> None:
    scope: Scope = {"type": "http", "method": "GET", "path": "/mcp", "headers": []}

    with pytest.raises(RuntimeError, match="secret"):
        await RequestContext(Exploding(after_start=True))(scope, idle, discard)


@pytest.mark.anyio
async def test_an_unhandled_error_still_answers_500_with_a_request_id_and_no_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    sent: list[Message] = []

    async def collect(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": "GET", "path": "/mcp", "headers": []}
    await RequestContext(Exploding())(scope, idle, collect)

    start = sent[0]
    assert start["status"] == 500
    assert any(k == b"x-request-id" for k, _ in cast("list[tuple[bytes, bytes]]", start["headers"]))
    assert b"secret" not in b"".join(cast("bytes", m.get("body", b"")) for m in sent)
    records = [r for r in caplog.records if r.getMessage() == "http request"]
    assert [field(r, "status") for r in records] == [500]
    assert "secret internal detail" not in caplog.text


@pytest.mark.anyio
async def test_the_logged_path_is_cut_to_256_characters(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    scope: Scope = {"type": "http", "method": "GET", "path": "/" + "a" * 1000, "headers": []}

    await RequestContext(Recorder())(scope, idle, discard)

    logged = [field(r, "path") for r in caplog.records if r.getMessage() == "http request"]
    assert [len(str(path)) for path in logged] == [256]


def test_the_real_resolver_knows_localhost_and_rejects_nonsense() -> None:
    assert is_loopback("localhost")
    assert not is_loopback("no-such-host.invalid")
