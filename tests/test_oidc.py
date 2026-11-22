"""The optional OIDC layer of the HTTP transport (decision-31).

A stub OpenID Connect issuer (``oidc_issuer``) listens on a loopback port; the server app runs
in-process behind an ASGI client, and a scripted browser walks the OAuth flow of a FastMCP
client: consent page, the issuer's login, the callback and the code exchange.
"""

import contextlib
import logging
import os
import re
from typing import TYPE_CHECKING, cast, override
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth import MultiAuth
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from harness import admin, refused_url, serve
from mcp.shared.auth import AuthorizationCodeResult
from oidc_issuer import CLIENT_ID, CLIENT_SECRET, Issuer, stub_issuer

from paperless_mcp.app import StaticBearerVerifier, authenticator, build_app, check_startup
from paperless_mcp.domain.errors import InvalidConfiguration
from paperless_mcp.settings import CLAUDE_CALLBACK, HttpSettings, OidcSettings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator
    from pathlib import Path

    from starlette.applications import Starlette

pytestmark = pytest.mark.decision("decision-31")

BASE = "https://mcp.example.test"
HOST = "mcp.example.test"
BEARER_TOKEN = "bearer-token-for-claude-code-0123"
SIGNING_KEY = "signing-key-of-the-test-server-0123456789"
MAX_UPLOAD = 1024 * 1024


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide PAPERLESS_* variables of the developer's shell from every test."""
    for name in os.environ:
        if name.upper().startswith("PAPERLESS_"):
            monkeypatch.delenv(name)


@pytest.fixture
def issuer() -> Iterator[Issuer]:
    with stub_issuer() as running:
        yield running


def resolve(host: str) -> list[str]:
    if host == "localhost":
        return ["127.0.0.1"]
    message = f"cannot resolve {host}"
    raise OSError(message)


def oidc_settings(issuer_url: str, **given: object) -> OidcSettings:
    values: dict[str, object] = {
        "config_url": f"{issuer_url}/.well-known/openid-configuration",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "base_url": BASE,
        "jwt_signing_key": SIGNING_KEY,
    }
    return OidcSettings.model_validate(values | given)


def http_settings(**given: object) -> HttpSettings:
    values: dict[str, object] = {"host": "0.0.0.0", "allowed_hosts": HOST}  # noqa: S104 -- a value under test
    return HttpSettings.model_validate(values | given)


def refusal(http: HttpSettings, oidc: OidcSettings) -> str:
    with pytest.raises(InvalidConfiguration) as caught:
        check_startup(http, resolve, oidc=oidc)
    return caught.value.describe()


# --- startup ---------------------------------------------------------------------------------


def test_oidc_alone_is_authentication_enough_to_start(issuer: Issuer) -> None:
    check_startup(http_settings(), resolve, oidc=oidc_settings(issuer.url))


def test_oidc_and_bearer_tokens_start_together(issuer: Issuer) -> None:
    check_startup(http_settings(auth_tokens=BEARER_TOKEN), resolve, oidc=oidc_settings(issuer.url))


def test_without_oidc_variables_nothing_changes() -> None:
    message = refusal(http_settings(), OidcSettings.model_validate({}))

    assert "PAPERLESS_MCP_HTTP_AUTH_TOKENS" in message
    assert "PAPERLESS_MCP_OIDC_" in message
    check_startup(http_settings(auth_tokens=BEARER_TOKEN), resolve, oidc=OidcSettings())


@pytest.mark.parametrize("given", [{"client_secret": None}, {"config_url": None, "base_url": None}])
def test_incomplete_oidc_settings_refuse_to_start_naming_what_is_missing(
    issuer: Issuer, given: dict[str, object]
) -> None:
    oidc = oidc_settings(issuer.url, **given)

    message = refusal(http_settings(auth_tokens=BEARER_TOKEN), oidc)

    assert "incomplete" in message
    for name in given:
        assert f"PAPERLESS_MCP_OIDC_{name.upper()}" in message
    assert "PAPERLESS_MCP_OIDC_CLIENT_ID" not in message
    assert CLIENT_SECRET not in message


def test_an_optional_oidc_variable_alone_refuses_to_start() -> None:
    oidc = OidcSettings.model_validate({"allowed_redirect_uris": CLAUDE_CALLBACK})

    message = refusal(http_settings(auth_tokens=BEARER_TOKEN), oidc)

    assert "PAPERLESS_MCP_OIDC_CONFIG_URL" in message


def test_the_base_url_host_must_be_an_allowed_host(issuer: Issuer) -> None:
    message = refusal(http_settings(allowed_hosts="other.example.test"), oidc_settings(issuer.url))

    assert "PAPERLESS_MCP_HTTP_ALLOWED_HOSTS, or browsers and clients get 421" in message
    assert HOST in message


def test_a_loopback_base_url_on_a_loopback_bind_needs_no_allowed_hosts(issuer: Issuer) -> None:
    check_startup(
        HttpSettings.model_validate({}),
        resolve,
        oidc=oidc_settings(issuer.url, base_url="http://localhost:8000"),
    )


def test_a_storage_directory_that_cannot_be_written_refuses_to_start(
    issuer: Issuer, tmp_path: Path
) -> None:
    absent = tmp_path / "absent"
    message = refusal(http_settings(), oidc_settings(issuer.url, storage_dir=str(absent)))

    assert "PAPERLESS_MCP_OIDC_STORAGE_DIR" in message
    assert str(absent) in message
    assert "keep the OAuth state in memory." in message


def test_a_file_is_no_storage_directory(issuer: Issuer, tmp_path: Path) -> None:
    file = tmp_path / "file"
    _ = file.write_text("")

    message = refusal(http_settings(), oidc_settings(issuer.url, storage_dir=str(file)))

    assert "is not a writable directory" in message


@pytest.mark.skipif(os.geteuid() == 0, reason="root may write to any directory")
def test_a_read_only_storage_directory_refuses_to_start(issuer: Issuer, tmp_path: Path) -> None:
    tmp_path.chmod(0o500)
    try:
        message = refusal(http_settings(), oidc_settings(issuer.url, storage_dir=str(tmp_path)))
    finally:
        tmp_path.chmod(0o700)

    assert "is not a writable directory" in message


# --- composition -----------------------------------------------------------------------------


def test_the_authentication_matches_the_configured_sources(issuer: Issuer) -> None:
    oidc = oidc_settings(issuer.url)

    assert authenticator(http_settings(), OidcSettings()) is None
    assert isinstance(authenticator(http_settings(auth_tokens=BEARER_TOKEN)), StaticBearerVerifier)
    alone = authenticator(http_settings(), oidc)
    both = authenticator(http_settings(auth_tokens=BEARER_TOKEN), oidc)

    assert isinstance(alone, MultiAuth)
    assert isinstance(alone.server, OIDCProxy)
    assert alone.verifiers == []
    assert isinstance(both, MultiAuth)
    assert isinstance(both.server, OIDCProxy)
    assert [type(v) for v in both.verifiers] == [StaticBearerVerifier]


def test_an_unreachable_issuer_refuses_to_start_with_a_clear_message() -> None:
    url = refused_url()

    with pytest.raises(InvalidConfiguration) as caught:
        _ = authenticator(http_settings(), oidc_settings(url))

    message = caught.value.describe()
    assert "discovery document" in message
    assert url in message
    assert re.search(r"\(\w+Error\)", message)  # the type of the error, never its text
    assert "identity provider is reachable from this server." in message
    assert CLIENT_SECRET not in message


def test_a_server_without_the_oidc_layer_the_settings_ask_for_is_refused(issuer: Issuer) -> None:
    oidc = oidc_settings(issuer.url)
    bearer_only = serve(admin(), auth=authenticator(http_settings(auth_tokens=BEARER_TOKEN)))

    with pytest.raises(InvalidConfiguration, match="authentication"):
        _ = build_app(
            bearer_only,
            http_settings(auth_tokens=BEARER_TOKEN),
            max_upload_bytes=MAX_UPLOAD,
            oidc=oidc,
        )
    with_oidc = serve(admin(), auth=authenticator(http_settings(), oidc))
    with pytest.raises(InvalidConfiguration, match="authentication"):
        _ = build_app(with_oidc, http_settings(), max_upload_bytes=MAX_UPLOAD)


# --- the running app -------------------------------------------------------------------------


def make_app(http: HttpSettings, oidc: OidcSettings) -> Starlette:
    server = serve(admin(), auth=authenticator(http, oidc))
    return build_app(server, http, max_upload_bytes=MAX_UPLOAD, oidc=oidc)


@contextlib.asynccontextmanager
async def running(app: Starlette) -> AsyncGenerator[httpx2.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as client,
    ):
        yield client


class Browser(OAuth):
    """A FastMCP OAuth client whose browser is scripted: approve, log in, follow redirects."""

    def __init__(self, app: Starlette, **kwargs: object) -> None:
        self.app_client: httpx2.AsyncClient = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE
        )
        self.landed: str | None = None
        super().__init__(httpx_client_factory=factory(app), **kwargs)  # pyright: ignore[reportArgumentType] -- kwargs are OAuth's

    async def _visit(self, url: str) -> httpx2.Response:
        """GET ``url`` as a browser would: on the app in-process, else on the stub issuer."""
        if url.startswith(BASE):
            return await self.app_client.get(url)
        async with httpx2.AsyncClient() as outside:
            return await outside.get(url)

    @override
    async def redirect_handler(self, authorization_url: str) -> None:
        consent = await self._visit(authorization_url)
        while consent.status_code in {302, 303}:
            consent = await self._visit(consent.headers["location"])
        assert consent.status_code == 200, consent.text
        fields = dict(re.findall(r'name="(txn_id|csrf_token)" value="([^"]+)"', consent.text))
        approved = await self.app_client.post(
            str(consent.url),
            data={**fields, "submit": "true", "action": "approve"},
            headers={"Origin": BASE},
        )
        assert approved.status_code == 302, approved.text
        location = approved.headers["location"]
        while location.startswith(("http://127.0.0.1", BASE)):
            response = await self._visit(location)
            assert response.status_code in {302, 303}, response.text
            location = response.headers["location"]
        self.landed = location

    @override
    async def callback_handler(self) -> AuthorizationCodeResult:
        assert self.landed is not None
        query = {k: v[0] for k, v in parse_qs(urlsplit(self.landed).query).items()}
        return AuthorizationCodeResult(
            code=query["code"], state=query.get("state"), iss=query.get("iss")
        )


def factory(app: Starlette):  # noqa: ANN201 -- the McpHttpClientFactory protocol
    def make(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kwargs: object,
    ) -> httpx2.AsyncClient:
        _ = kwargs
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=BASE,
            headers=headers,
            timeout=timeout or httpx2.Timeout(30),
            auth=auth,
        )

    return make


async def tool_names(app: Starlette, auth: httpx2.Auth | None, headers: dict[str, str]) -> set[str]:
    with contextlib.ExitStack() as stack:
        if auth is not None:  # the test client keeps its tokens in memory, as it should
            _ = stack.enter_context(pytest.warns(UserWarning, match="in-memory token storage"))
        transport = StreamableHttpTransport(
            f"{BASE}/mcp", headers=headers, auth=auth, httpx_client_factory=factory(app)
        )
    async with Client(transport) as client:
        return {tool.name for tool in await client.list_tools()}


@pytest.mark.anyio
async def test_a_fastmcp_client_signs_in_through_the_oidc_issuer(
    issuer: Issuer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with app.router.lifespan_context(app):
        names = await tool_names(app, Browser(app), {})

    assert "get_instance_overview" in names
    [login] = issuer.authorizations
    assert login["redirect_uri"] == f"{BASE}/auth/callback"
    assert "openid" in login["scope"].split()
    assert login["code_challenge_method"] == "S256"
    [exchange] = issuer.token_requests
    assert exchange["grant_type"] == "authorization_code"
    # Nothing secret reached the log: neither the configuration nor anything the issuer handed out.
    logged = "\n".join(f"{r.getMessage()} {vars(r)}" for r in caplog.records if r.name != "httpx2")
    for secret in (CLIENT_SECRET, SIGNING_KEY, *issuer.issued):
        assert secret not in logged


@pytest.mark.anyio
async def test_claude_code_keeps_its_bearer_token_beside_oidc(issuer: Issuer) -> None:
    http = http_settings(auth_tokens=BEARER_TOKEN)
    app = make_app(http, oidc_settings(issuer.url))

    async with app.router.lifespan_context(app):
        names = await tool_names(app, None, {"Authorization": f"Bearer {BEARER_TOKEN}"})
        oauth_names = await tool_names(app, Browser(app), {})

    assert "get_instance_overview" in names
    assert names == oauth_names


@pytest.mark.anyio
async def test_a_wrong_token_gets_401_pointing_at_the_resource_metadata(issuer: Issuer) -> None:
    app = make_app(http_settings(auth_tokens=BEARER_TOKEN), oidc_settings(issuer.url))

    async with running(app) as client:
        response = await client.post(
            "/mcp",
            json={},
            headers={"Authorization": f"Bearer {BEARER_TOKEN}x", "Accept": "application/json"},
        )

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer")
    assert f"{BASE}/.well-known/oauth-protected-resource/mcp" in challenge


async def register(client: httpx2.AsyncClient, redirect_uri: str) -> httpx2.Response:
    return await client.post(
        "/register",
        json={
            "redirect_uris": [redirect_uri],
            "client_name": "connector",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )


async def authorize(client: httpx2.AsyncClient, client_id: str, redirect_uri: str) -> str:
    """Where an authorization request leads: the consent page, or an error (status and text)."""
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": "state-1",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "code_challenge_method": "S256",
        },
    )
    if response.status_code == 302:
        return f"302 {response.headers['location']}"
    return f"{response.status_code} {response.text[:200]}"


@pytest.mark.anyio
async def test_the_claude_ai_callback_is_allowed_and_a_foreign_one_is_not(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        claude = await register(client, CLAUDE_CALLBACK)
        foreign = await register(client, "https://evil.example/callback")
        client_id = cast("str", claude.json()["client_id"])
        consent = await authorize(client, client_id, CLAUDE_CALLBACK)
        elsewhere = await authorize(client, client_id, "https://evil.example/callback")

    assert claude.status_code == 201
    assert foreign.status_code == 400
    # The consent page: the user is asked before anything else happens.
    assert consent.startswith(f"302 {BASE}/consent?txn_id=")
    assert elsewhere.startswith("400 ")
    assert "does not match allowed patterns" in elsewhere
    assert foreign.json()["error"] == "invalid_redirect_uri"


@pytest.mark.anyio
async def test_a_consent_form_from_another_origin_is_refused(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        foreign = await client.post("/consent", data={}, headers={"Origin": "https://evil.example"})
        own = await client.post("/consent", data={}, headers={"Origin": BASE})

    assert foreign.status_code == 403
    assert own.status_code == 400  # past the guard: no transaction


@pytest.mark.anyio
@pytest.mark.parametrize("base", [BASE, f"{BASE}:8443"])
async def test_behind_a_tls_proxy_the_consent_form_of_the_public_origin_passes(
    issuer: Issuer, base: str
) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url, base_url=base))
    # The proxy forwards plain http; the browser's Origin is the public https origin.
    plain = base.replace("https://", "http://")
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=plain) as client,
    ):
        own = await client.post("/consent", data={}, headers={"Origin": base})
        other_port = await client.post("/consent", data={}, headers={"Origin": f"{BASE}:9443"})

    assert own.status_code == 400  # past the guard: no transaction
    assert other_port.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/token", "/register", "/.well-known/oauth-authorization-server"])
async def test_the_oauth_routes_grant_no_cross_origin_access(issuer: Issuer, path: str) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        preflight = await client.options(
            path,
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )

    assert preflight.status_code == 403
    assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.anyio
async def test_the_oidc_layer_adds_the_oauth_routes_and_no_others(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        server = await client.get("/.well-known/oauth-authorization-server")
        resource = await client.get("/.well-known/oauth-protected-resource/mcp")
        health = await client.get("/health")

    assert server.status_code == 200
    assert cast("str", server.json()["issuer"]).rstrip("/") == BASE
    assert resource.status_code == 200
    assert resource.json()["resource"] == f"{BASE}/mcp"
    assert health.status_code == 404
    assert {cast("str", getattr(route, "path", "")) for route in app.routes} == {
        "/mcp",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/mcp",
        "/authorize",
        "/token",
        "/register",
        "/consent",
        "/auth/callback",
    }


def stored_text(directory: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in directory.rglob("*") if p.is_file())


@pytest.mark.anyio
async def test_registrations_survive_a_restart_with_a_storage_directory(
    issuer: Issuer, tmp_path: Path
) -> None:
    oidc = oidc_settings(issuer.url, storage_dir=str(tmp_path))

    async with running(make_app(http_settings(), oidc)) as client:
        client_id = cast("str", (await register(client, CLAUDE_CALLBACK)).json()["client_id"])
    async with running(make_app(http_settings(), oidc)) as client:
        again = await authorize(client, client_id, CLAUDE_CALLBACK)
    stored = stored_text(tmp_path)

    assert again.startswith(f"302 {BASE}/consent?txn_id=")
    assert stored  # something was written, and it is encrypted:
    assert CLAUDE_CALLBACK not in stored


@pytest.mark.anyio
async def test_a_new_signing_key_forgets_the_stored_registrations_without_failing(
    issuer: Issuer, tmp_path: Path
) -> None:
    oidc = oidc_settings(issuer.url, storage_dir=str(tmp_path))
    rotated = oidc_settings(issuer.url, storage_dir=str(tmp_path), jwt_signing_key="x" * 40)

    async with running(make_app(http_settings(), oidc)) as client:
        client_id = cast("str", (await register(client, CLAUDE_CALLBACK)).json()["client_id"])
    async with running(make_app(http_settings(), rotated)) as client:
        again = await authorize(client, client_id, CLAUDE_CALLBACK)

    assert again.startswith("400 ")
    assert "is not registered" in again


@pytest.mark.anyio
async def test_without_a_storage_directory_a_restart_forgets_the_registrations(
    issuer: Issuer,
) -> None:
    oidc = oidc_settings(issuer.url)

    async with running(make_app(http_settings(), oidc)) as client:
        client_id = cast("str", (await register(client, CLAUDE_CALLBACK)).json()["client_id"])
    async with running(make_app(http_settings(), oidc)) as client:
        again = await authorize(client, client_id, CLAUDE_CALLBACK)

    assert again.startswith("400 ")
    assert "is not registered" in again
