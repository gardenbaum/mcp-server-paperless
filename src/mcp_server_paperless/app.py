"""ASGI entrypoint for HTTP deployments: secure by default (decision-27, decision-31).

``check_startup`` refuses a configuration that would expose the server without authentication;
``authenticator`` builds the authentication (static bearer tokens, the optional OIDC layer, or
both); ``build_app`` wraps a server in the ASGI app with that authentication, the host and origin
guard, a request id and a body limit.
"""

import base64
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

import uvicorn
from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from fastmcp.server.http import StreamableHTTPASGIApp
from fastmcp.utilities.ui import FASTMCP_LOGO_URL
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.types import INVALID_REQUEST
from pydantic import AnyHttpUrl, ValidationError

from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.observability import RequestContext, request_header
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Sequence

    from fastmcp import FastMCP
    from key_value.aio.protocols import AsyncKeyValue
    from pydantic import HttpUrl, SecretStr
    from starlette.applications import Starlette
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from mcp_server_paperless.settings import (
        HttpSettings,
        OidcSettings,
        PaperlessSettings,
        ServerSettings,
    )

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


def check_startup(
    http: HttpSettings, resolve: Resolver = resolve_addresses, *, oidc: OidcSettings | None = None
) -> None:
    """Refuse to start an HTTP server that would be open to anyone who can reach it.

    Raises:
        InvalidConfiguration: The OIDC layer is configured but incomplete, its base URL names a
            host that is not allowed or its storage directory cannot be written; neither tokens
            nor the OIDC layer are configured and the bind is not loopback with the explicit
            flag; or a remote bind names no allowed hosts.
    """
    loopback = is_loopback(http.host, resolve)
    with_oidc = oidc is not None and oidc.configured
    if oidc is not None and (missing := oidc.missing()):
        message = (
            f"the OIDC layer is incomplete: set {', '.join(missing)}, or unset every "
            "PAPERLESS_MCP_OIDC_* variable to serve with bearer tokens alone."
        )
        raise InvalidConfiguration(message)
    if not http.auth_tokens and not with_oidc:
        if not loopback:
            message = (
                f"refusing to serve {http.host!r} without authentication: set "
                "PAPERLESS_MCP_HTTP_AUTH_TOKENS or the PAPERLESS_MCP_OIDC_* variables. The "
                "unauthenticated mode is only for a loopback bind, and this bind is not loopback."
            )
            raise InvalidConfiguration(message, hint="Generate a token with: openssl rand -hex 32")
        if not http.allow_unauthenticated_loopback:
            message = (
                "refusing to start without authentication: set PAPERLESS_MCP_HTTP_AUTH_TOKENS or "
                "the PAPERLESS_MCP_OIDC_* variables, or "
                "PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK=true to serve "
                f"{http.host!r} without it (loopback only)."
            )
            raise InvalidConfiguration(message)
    if not loopback and not http.allowed_hosts:
        message = (
            f"{http.host!r} is not a loopback address: name the host names clients use in "
            "PAPERLESS_MCP_HTTP_ALLOWED_HOSTS."
        )
        raise InvalidConfiguration(message)
    if with_oidc:
        _check_oidc(http, cast("OidcSettings", oidc))


# The host names the host and origin guard always lets through (FastMCP's DEFAULT_HOSTS).
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


def _check_oidc(http: HttpSettings, oidc: OidcSettings) -> None:
    host = (cast("HttpUrl", oidc.base_url).host or "").lower()
    if host not in LOOPBACK_HOSTS and host not in http.allowed_hosts:
        message = (
            f"the OIDC base URL names {host!r}, which is not an allowed host: add it to "
            "PAPERLESS_MCP_HTTP_ALLOWED_HOSTS, or browsers and clients get 421 from every route."
        )
        raise InvalidConfiguration(message)
    directory = oidc.storage_dir
    if directory is not None and not (
        Path(directory).is_dir() and os.access(directory, os.W_OK | os.X_OK)
    ):
        message = (
            f"PAPERLESS_MCP_OIDC_STORAGE_DIR {directory!r} is not a writable directory: create "
            "it (a volume in a container) or unset the variable to keep the OAuth state in memory."
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


# Seconds the discovery request may take at startup (FastMCP's default).
DISCOVERY_TIMEOUT = 10
# The one scope the identity provider is asked for: it returns the ID token this server checks.
OIDC_SCOPES = ["openid"]


# Most entries the in-memory store keeps per collection (registrations, transactions, codes,
# tokens); beyond it the oldest are dropped. /register and /authorize need no authentication, so
# without a cap a flood of them would grow memory without bound (decision-31).
MEMORY_ENTRIES = 10_000
STORAGE_SALT = "paperless-mcp-oauth-storage"


def oauth_storage(oidc: OidcSettings) -> AsyncKeyValue:
    """Where the OIDC layer keeps client registrations, sessions and upstream tokens.

    In memory without ``storage_dir`` (capped per collection); else files there, with FastMCP's
    file-name sanitising (any key stays inside its collection directory) and encrypted with a
    Fernet key stretched from the signing key by PBKDF2, as FastMCP stretches a string signing
    key. FastMCP's own default store lives in a home directory the container does not have.
    """
    if oidc.storage_dir is None:
        return MemoryStore(max_entries_per_collection=MEMORY_ENTRIES)
    signing_key = cast("SecretStr", oidc.jwt_signing_key).get_secret_value()
    fernet_key = derive_jwt_key(low_entropy_material=signing_key, salt=STORAGE_SALT)
    directory = Path(oidc.storage_dir)
    files = FileTreeStore(
        data_directory=directory,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
    )
    # A key that no longer decrypts (a new signing key) is a miss: clients register again.
    return FernetEncryptionWrapper(
        key_value=files, fernet=Fernet(key=fernet_key), raise_on_decryption_error=False
    )


def oidc_proxy(oidc: OidcSettings) -> OIDCProxy:
    """FastMCP's OIDC proxy in front of the configured identity provider (decision-31).

    It reads the discovery document now. The identity is the provider's ID token (audience: the
    client id), which every OIDC provider issues as a signed JWT, whatever its access tokens are.
    The consent page stays on, CIMD and dynamic registration are offered, and clients may only
    register the allowed callbacks.

    Raises:
        InvalidConfiguration: The discovery document cannot be read or lacks an endpoint.
    """
    config_url = str(oidc.config_url)
    # Discovery first, on its own: FastMCP caches the document briefly, so the proxy below
    # reuses it. It fails with httpx2's errors (this module may not import httpx2) or with
    # pydantic's for a malformed document; the messages name the type only.
    try:
        _ = OIDCConfiguration.get_oidc_configuration(
            AnyHttpUrl(config_url), strict=None, timeout_seconds=DISCOVERY_TIMEOUT
        )
    except ValidationError:
        message = (
            f"the document at {config_url} is no valid OpenID Connect discovery document "
            "(ValidationError): it must name the issuer, an authorization and a token endpoint "
            "and the signing keys (jwks_uri). Check PAPERLESS_MCP_OIDC_CONFIG_URL."
        )
        raise InvalidConfiguration(message) from None
    except Exception as error:  # noqa: BLE001 -- see above
        message = (
            f"could not read the OIDC discovery document at {config_url} "
            f"({type(error).__name__}): check PAPERLESS_MCP_OIDC_CONFIG_URL and that the "
            "identity provider is reachable from this server."
        )
        raise InvalidConfiguration(message) from None
    try:
        return OIDCProxy(
            config_url=config_url,
            client_id=cast("str", oidc.client_id),
            client_secret=cast("SecretStr", oidc.client_secret).get_secret_value(),
            base_url=str(oidc.base_url).rstrip("/"),
            jwt_signing_key=cast("SecretStr", oidc.jwt_signing_key).get_secret_value(),
            timeout_seconds=DISCOVERY_TIMEOUT,
            verify_id_token=True,
            required_scopes=OIDC_SCOPES,
            allowed_client_redirect_uris=list(oidc.allowed_redirect_uris),
            client_storage=oauth_storage(oidc),
            require_authorization_consent=True,
            enable_cimd=True,
        )
    except Exception as error:  # noqa: BLE001 -- FastMCP raises ValueError and others
        message = (
            f"could not set up the OIDC proxy from the discovery document at {config_url} "
            f"({type(error).__name__}): check the client id, secret and signing key."
        )
        raise InvalidConfiguration(message) from None


def authenticator(http: HttpSettings, oidc: OidcSettings | None = None) -> AuthProvider | None:
    """The authentication of the endpoint (decision-31).

    Static bearer tokens alone; with the OIDC layer, FastMCP's ``MultiAuth`` with the OIDC proxy
    first and the bearer tokens, if any, after it; None while neither is configured.

    Raises:
        InvalidConfiguration: See ``oidc_proxy``.
    """
    bearer = StaticBearerVerifier(http.auth_tokens) if http.auth_tokens else None
    if oidc is None or not oidc.configured:
        return bearer
    # required_scopes=[]: the proxy checks the openid scope of its own sessions; the bearer
    # tokens carry no scopes and must not be asked for one.
    return MultiAuth(
        server=oidc_proxy(oidc),
        verifiers={"static-bearer": bearer} if bearer is not None else {},
        required_scopes=[],
    )


# Most bytes of a request body anywhere but at the MCP endpoint: the OAuth routes need no
# authentication, and what is registered there is stored and shown (decision-39).
OTHER_BODY_LIMIT = 64 * 1024
# Room for the JSON-RPC envelope and the other arguments around base64 content.
ENVELOPE_BYTES = 64 * 1024


def body_limit(max_upload_bytes: int) -> int:
    """Most bytes of a request body: the base64 text of the largest upload plus the envelope.

    The tools check the size of base64 content themselves, but only after the body was read and
    parsed; this bounds what is read first.
    """
    return -(-max_upload_bytes // 3) * 4 + ENVELOPE_BYTES


async def _reply(send: Send, status: int, answer: object) -> None:
    body = json.dumps(answer).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _too_large(send: Send, limit: int) -> None:
    """413 with a JSON-RPC error: the SDK's client shows its message, and no other body's."""
    message = (
        f"Request too large: this server takes at most {limit} bytes per request, which "
        "follows PAPERLESS_MCP_MAX_UPLOAD_BYTES (at most 100 MiB). Send less, such as a smaller "
        "file."
    )
    error = {"code": INVALID_REQUEST, "message": message}
    await _reply(send, 413, {"jsonrpc": "2.0", "id": None, "error": error})


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
            await _too_large(send, self.limit)
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
            await _too_large(send, self.limit)


NO_SDK_LIMIT = (
    "cannot set the request body limit of the MCP endpoint: this version of FastMCP or the MCP SDK "
    "keeps it elsewhere (decision-40)."
)


def raise_sdk_limit(app: Starlette, limit: int) -> None:
    """Let the MCP endpoint of ``app`` take bodies of ``limit`` bytes (decision-40).

    The MCP SDK refuses bodies over 4 MiB, and FastMCP has no option to change that. The manager
    with the SDK's limit is made at each start of the app, so it is set right after.

    Raises:
        InvalidConfiguration: FastMCP's endpoint is not among the routes. Starting the app raises
            it when the limit is not where it is expected, so a server with another limit never
            serves; it then comes out of the session manager's task group, in an ExceptionGroup.
    """
    endpoints = [getattr(route, "endpoint", None) for route in app.routes]
    # With authentication FastMCP wraps its endpoint.
    inner = [getattr(endpoint, "app", endpoint) for endpoint in endpoints]
    served = next((e for e in inner if isinstance(e, StreamableHTTPASGIApp)), None)
    if served is None:
        raise InvalidConfiguration(NO_SDK_LIMIT)
    start = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def raised(starting: Starlette) -> AsyncGenerator[None]:
        async with start(starting):
            limiter = getattr(served.session_manager, "asgi_app", None)
            if not isinstance(limiter, RequestBodyLimitMiddleware) or not hasattr(
                limiter, "max_body_size"
            ):
                raise InvalidConfiguration(NO_SDK_LIMIT)
            limiter.max_body_size = limit
            yield

    app.router.lifespan_context = raised


class SmallBodies:
    """ASGI middleware: 413 for a body over ``OTHER_BODY_LIMIT`` anywhere but at ``mcp_path``.

    Only that exact path is let through: a route nobody thought of is limited, too. The SDK's
    middleware reads the body, at most the limit, before the app is called, so that no handler
    can catch the refusal.
    """

    def __init__(self, app: ASGIApp, mcp_path: str | None) -> None:
        """Wrap ``app``; ``mcp_path`` is the MCP endpoint, None to limit every path."""
        self.app: ASGIApp = app
        self.limited: ASGIApp = RequestBodyLimitMiddleware(app, OTHER_BODY_LIMIT)
        self.mcp_path: str | None = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI connection."""
        app = self.app if scope.get("path") == self.mcp_path else self.limited
        await app(scope, receive, send)


ICON = base64.b64encode(Path(__file__).with_name("icon.svg").read_bytes())
ICON_SRC = b'src="data:image/svg+xml;base64,' + ICON + b'"'
# With the quotes: FastMCP escapes them in what a client or provider sent, so only its own tag fits.
LOGO_SRC = f'src="{FASTMCP_LOGO_URL}"'.encode()


class OwnIcon:
    """ASGI middleware: HTML pages show this project's icon instead of FastMCP's logo.

    FastMCP's sign-in pages load that logo from its website, which would see the user's browser.
    Its ``icons`` setting would not do: the error pages of the callback ignore it, and it would
    send the icon to every MCP client.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI connection."""
        start: Message | None = None
        page = b""

        async def shown(message: Message) -> None:
            nonlocal start, page
            if message["type"] == "http.response.start":
                headers = dict(cast("list[tuple[bytes, bytes]]", message["headers"]))
                if headers.get(b"content-type", b"").startswith(b"text/html"):
                    start = message
                    return
            if start is None:
                await send(message)
                return
            # The whole page first: its length changes, and the URL may span two chunks.
            page += cast("bytes", message.get("body", b""))
            if message.get("more_body", False):
                return
            page = page.replace(LOGO_SRC, ICON_SRC, 1)
            kept = cast("list[tuple[bytes, bytes]]", start["headers"])
            start["headers"] = [
                *(header for header in kept if header[0] != b"content-length"),
                (b"content-length", str(len(page)).encode()),
            ]
            await send(start)
            await send({"type": "http.response.body", "body": page})

        await self.app(scope, receive, shown)


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
                await _reply(send, 403, {"error": message})
                return
            if any(request_header(scope, name) is not None for name in PROXY_HEADERS):
                message = "no authentication is configured: this app does not sit behind a proxy"
                await _reply(send, 403, {"error": message})
                return
        await self.app(scope, receive, send)


DEFAULT_PORTS = {"http": 80, "https": 443}


def public_origin(oidc: OidcSettings) -> str:
    """The browser origin of the OIDC base URL: without a port only when it is the scheme's own."""
    url = cast("HttpUrl", oidc.base_url)
    port = "" if url.port in {None, DEFAULT_PORTS[url.scheme]} else f":{url.port}"
    return f"{url.scheme}://{url.host}{port}"


def build_app(
    server: FastMCP,
    http: HttpSettings,
    *,
    max_upload_bytes: int,
    oidc: OidcSettings | None = None,
) -> Starlette:
    """The ASGI app of ``server``: authentication, host and origin guard, body limit.

    With the OIDC layer the only browser origin allowed is the server's own public origin (its
    consent form posts there; behind a TLS proxy the request itself arrives as plain http).

    Raises:
        InvalidConfiguration: ``server`` was built without the authentication the settings ask
            for, or with authentication they do not ask for, or see ``raise_sdk_limit``.
    """
    with_oidc = oidc is not None and oidc.configured
    authenticated = bool(http.auth_tokens) or with_oidc
    if (server.auth is None) == authenticated or with_oidc != isinstance(server.auth, MultiAuth):
        message = "the server's authentication does not match the HTTP settings"
        raise InvalidConfiguration(message)
    origins = [public_origin(cast("OidcSettings", oidc))] if with_oidc else []
    # Every option is explicit: FASTMCP_* environment variables must not loosen the app (a
    # wildcard origin, debug tracebacks, stateless routes, no session expiry).
    app = server.http_app(
        path=http.path,
        json_response=False,
        stateless_http=False,
        host_origin_protection=True,
        allowed_hosts=list(http.allowed_hosts),
        allowed_origins=origins,
        session_idle_timeout="auto",
    )
    app.debug = False
    raise_sdk_limit(app, body_limit(max_upload_bytes))
    # /mcp/ is not /mcp: a redirect would be answered before authentication, and with the
    # wrong port and scheme behind a proxy.
    app.router.redirect_slashes = False
    # An MCP path that is also an OAuth route's path would raise that route's limit.
    alone = [getattr(route, "path", None) for route in app.routes].count(http.path) == 1
    app.add_middleware(BodyLimit, limit=body_limit(max_upload_bytes))
    app.add_middleware(SmallBodies, mcp_path=http.path if alone else None)
    if with_oidc:
        app.add_middleware(OwnIcon)
    if not authenticated:
        app.add_middleware(LoopbackOnly)
    # Outermost, so that a refusal by the host guard or the authentication has an id and a record.
    app.add_middleware(RequestContext)
    return app


def create_app(
    paperless: PaperlessSettings,
    settings: ServerSettings,
    http: HttpSettings,
    *,
    oidc: OidcSettings | None = None,
    resolve: Resolver = resolve_addresses,
) -> Starlette:
    """The ASGI app of the mcp-server-paperless server (``mcp-server-paperless-http``).

    Never offers a path parameter: HTTP callers upload base64 content (decision-7).

    Raises:
        InvalidConfiguration: See ``check_startup``, ``oidc_proxy`` and ``raise_sdk_limit``, or
            the selection leaves no tool.
    """
    check_startup(http, resolve, oidc=oidc)
    server = create_server(paperless, settings, stdio=False, auth=authenticator(http, oidc))
    return build_app(server, http, max_upload_bytes=settings.max_upload_bytes, oidc=oidc)


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
