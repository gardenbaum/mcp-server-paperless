"""ASGI entrypoint for HTTP deployments: secure by default (decision-27).

``check_startup`` refuses a configuration that would expose the server without authentication;
``build_app`` wraps a server in the ASGI app with bearer authentication, the host and origin
guard, a request id and a body limit.
"""

import hashlib
import hmac
import ipaddress
import json
import socket
from typing import TYPE_CHECKING, cast, override

import uvicorn
from fastmcp.server.auth import AccessToken, TokenVerifier

from paperless_mcp.domain.errors import InvalidConfiguration
from paperless_mcp.observability import RequestContext, request_header
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastmcp import FastMCP
    from pydantic import SecretStr
    from starlette.applications import Starlette
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from paperless_mcp.settings import HttpSettings, PaperlessSettings, ServerSettings

type Resolver = Callable[[str], Sequence[str]]


def resolve_addresses(host: str) -> list[str]:
    """The addresses ``host`` resolves to (``OSError`` when it does not)."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _loopback_address(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    # An IPv4-mapped address is as loopback as the IPv4 address inside it.
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def is_loopback(host: str, resolve: Resolver = resolve_addresses) -> bool:
    """Whether binding ``host`` reaches the local machine only.

    An IP address is judged by itself (IPv4-mapped IPv6 included); a name only if every address
    it resolves to is loopback. A name that does not resolve, the empty string and the
    unspecified addresses (0.0.0.0, ::) are remote.
    """
    bare = host.strip().removeprefix("[").removesuffix("]")
    if not bare:
        return False
    try:
        _ = ipaddress.ip_address(bare.split("%", 1)[0])
    except ValueError:
        try:
            addresses = resolve(bare.lower())
        except OSError:
            return False
        return bool(addresses) and all(_loopback_address(a) for a in addresses)
    return _loopback_address(bare)


def check_startup(http: HttpSettings, resolve: Resolver = resolve_addresses) -> None:
    """Refuse to start an HTTP server that would be open to anyone who can reach it.

    Raises:
        InvalidConfiguration: No tokens are configured and the bind is not loopback with the
            explicit flag, or a remote bind names no allowed hosts.
    """
    loopback = is_loopback(http.host, resolve)
    if not http.auth_tokens:
        if not loopback:
            message = (
                f"refusing to serve {http.host!r} without authentication: set "
                "PAPERLESS_MCP_HTTP_AUTH_TOKENS. The unauthenticated mode is only for a "
                "loopback bind, and this bind is not loopback."
            )
            raise InvalidConfiguration(message, hint="Generate a token with: openssl rand -hex 32")
        if not http.allow_unauthenticated_loopback:
            message = (
                "refusing to start without authentication: set PAPERLESS_MCP_HTTP_AUTH_TOKENS, "
                "or PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK=true to serve "
                f"{http.host!r} without it (loopback only)."
            )
            raise InvalidConfiguration(message)
    if not loopback and not http.allowed_hosts:
        message = (
            f"{http.host!r} is not a loopback address: name the host names clients use in "
            "PAPERLESS_MCP_HTTP_ALLOWED_HOSTS."
        )
        raise InvalidConfiguration(message)


class StaticBearerVerifier(TokenVerifier):
    """Accepts a bearer token that equals one of the configured tokens.

    Only SHA-256 digests are kept, and a presented token is compared with every digest in
    constant time (``hmac.compare_digest`` on fixed-length digests, no early exit), so neither
    the length of a token nor the position of the first differing byte shows in the timing.
    The ``AccessToken`` carries a label, never the token.
    """

    def __init__(self, tokens: Sequence[SecretStr]) -> None:
        """Keep the digests of ``tokens``."""
        super().__init__()
        self._digests: tuple[bytes, ...] = tuple(
            hashlib.sha256(token.get_secret_value().encode()).digest() for token in tokens
        )

    @override
    async def verify_token(self, token: str) -> AccessToken | None:
        """The access of the matching token, or None."""
        digest = hashlib.sha256(token.encode()).digest()
        matched: int | None = None
        for index, known in enumerate(self._digests):
            if hmac.compare_digest(digest, known):
                matched = index
        if matched is None:
            return None
        return AccessToken(
            token=f"static-token-{matched}", client_id=f"static-token-{matched}", scopes=[]
        )


def authenticator(http: HttpSettings) -> TokenVerifier | None:
    """The authentication of the endpoint: static bearer tokens, or None while there are none."""
    return StaticBearerVerifier(http.auth_tokens) if http.auth_tokens else None


# Room for the JSON-RPC envelope and the other arguments around base64 content.
ENVELOPE_BYTES = 64 * 1024


def body_limit(max_upload_bytes: int) -> int:
    """Most bytes of a request body: the base64 text of the largest upload plus the envelope.

    The tools check the size of base64 content themselves, but only after the body was read and
    parsed; this bounds what is read first.
    """
    return -(-max_upload_bytes // 3) * 4 + ENVELOPE_BYTES


async def _reply(send: Send, status: int, text: str) -> None:
    body = json.dumps({"error": text}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class BodyTooLargeError(Exception):
    """The request body went past the limit while the app was reading it."""


class BodyLimit:
    """ASGI middleware: refuses a request body larger than ``limit`` bytes with 413.

    A declared ``Content-Length`` over the limit is refused without reading anything; a body
    without one (chunked) is cut off as soon as it passes the limit.
    """

    def __init__(self, app: ASGIApp, limit: int) -> None:
        """Wrap ``app``; ``limit`` is the most body bytes it may receive."""
        self.app: ASGIApp = app
        self.limit: int = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI connection."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = request_header(scope, b"content-length") or b"0"
        if declared.isdigit() and int(declared) > self.limit:
            await _reply(send, 413, "request body too large")
            return
        received = 0
        started = False

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(cast("bytes", message.get("body", b"")))
                if received > self.limit:
                    raise BodyTooLargeError
            return message

        async def watched(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except BodyTooLargeError:
            if started:
                raise
            await _reply(send, 413, "request body too large")


PROXY_HEADERS = (b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-real-ip")


class LoopbackOnly:
    """ASGI middleware for the unauthenticated mode: answers only on a loopback address.

    ``check_startup`` already refuses a configuration that binds elsewhere; this stops an app
    that is started by other means (uvicorn on the command line with another ``--host``).
    Only an IP literal counts (uvicorn reports one; a name is never looked up per request).
    A request that carries proxy headers is refused too: behind a local reverse proxy every
    client would look like loopback, so this mode must never sit behind one.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI connection."""
        if scope["type"] == "http":
            server = cast("tuple[str, int | None] | None", scope.get("server"))
            if not (server and _loopback_address(server[0])):
                message = "no authentication is configured: this app answers on loopback only"
                await _reply(send, 403, message)
                return
            if any(request_header(scope, name) is not None for name in PROXY_HEADERS):
                message = "no authentication is configured: this app does not sit behind a proxy"
                await _reply(send, 403, message)
                return
        await self.app(scope, receive, send)


def build_app(server: FastMCP, http: HttpSettings, *, max_upload_bytes: int) -> Starlette:
    """The ASGI app of ``server``: bearer authentication, host and origin guard, body limit.

    Raises:
        InvalidConfiguration: ``server`` was built without the authentication ``http`` asks
            for, or with authentication ``http`` does not ask for.
    """
    if (server.auth is None) != (not http.auth_tokens):
        message = "the server's authentication does not match the HTTP settings"
        raise InvalidConfiguration(message)
    # Every option is explicit: FASTMCP_* environment variables must not loosen the app (a
    # wildcard origin, debug tracebacks, stateless routes, no session expiry).
    app = server.http_app(
        path=http.path,
        json_response=False,
        stateless_http=False,
        host_origin_protection=True,
        allowed_hosts=list(http.allowed_hosts),
        allowed_origins=[],
        session_idle_timeout="auto",
    )
    app.debug = False
    # /mcp/ is not /mcp: a redirect would be answered before authentication, and with the
    # wrong port and scheme behind a proxy.
    app.router.redirect_slashes = False
    app.add_middleware(BodyLimit, limit=body_limit(max_upload_bytes))
    if not http.auth_tokens:
        app.add_middleware(LoopbackOnly)
    # Outermost, so that a refusal by the host guard or the authentication has an id and a record.
    app.add_middleware(RequestContext)
    return app


def create_app(
    paperless: PaperlessSettings,
    settings: ServerSettings,
    http: HttpSettings,
    *,
    resolve: Resolver = resolve_addresses,
) -> Starlette:
    """The ASGI app of the paperless-mcp server (``paperless-mcp-http``).

    Never offers a path parameter: HTTP callers upload base64 content (decision-7).

    Raises:
        InvalidConfiguration: See ``check_startup``, or the selection leaves no tool.
    """
    check_startup(http, resolve)
    server = create_server(paperless, settings, stdio=False, auth=authenticator(http))
    return build_app(server, http, max_upload_bytes=settings.max_upload_bytes)


def uvicorn_server(app: Starlette, http: HttpSettings) -> uvicorn.Server:
    """A uvicorn server for ``app`` bound to ``http.host`` and ``http.port``.

    No access log (it prints query strings; ``RequestContext`` logs each request without them),
    no trust in forwarded headers, no server banner and no WebSocket support (uvicorn logs a
    handshake with its query string; there is no WebSocket route); its loggers use the root handler.
    """
    config = uvicorn.Config(
        app,
        host=http.host,
        port=http.port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        ws="none",
    )
    return uvicorn.Server(config)


def run_server(server: uvicorn.Server) -> bool:
    """Serve until stopped; whether the server came up at all."""
    server.run()
    return server.started
