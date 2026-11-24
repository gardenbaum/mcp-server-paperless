"""The optional OIDC layer of the HTTP transport (decision-31).

A stub OpenID Connect issuer (``oidc_issuer``) listens on a loopback port; the server app runs
in-process behind an ASGI client, and a scripted browser walks the OAuth flow of a FastMCP
client: consent page, the issuer's login, the callback and the code exchange.
"""

import base64
import contextlib
import logging
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, cast, override
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import pytest
from cryptography.fernet import Fernet
from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth import MultiAuth
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from harness import admin, refused_url, serve
from joserfc import jwk, jwt
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.shared.auth import AuthorizationCodeResult
from oidc_issuer import CLIENT_ID, CLIENT_SECRET, Issuer, stub_issuer

import mcp_server_paperless
from mcp_server_paperless.app import (
    MEMORY_ENTRIES,
    OwnIcon,
    StaticBearerVerifier,
    authenticator,
    body_limit,
    build_app,
    check_startup,
    oauth_storage,
    public_origin,
)
from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.settings import (
    CLAUDE_CALLBACK,
    CLAUDE_COM_CALLBACK,
    HttpSettings,
    OidcSettings,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterator

    from starlette.applications import Starlette
    from starlette.types import Message, Receive, Scope, Send

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


def test_a_discovery_document_without_a_token_endpoint_is_not_called_unreadable(
    issuer: Issuer,
) -> None:
    issuer.left_out = {"token_endpoint"}

    with pytest.raises(InvalidConfiguration) as caught:
        _ = authenticator(http_settings(), oidc_settings(issuer.url))

    message = caught.value.describe()
    assert "could not read" not in message
    assert "is no valid OpenID Connect discovery document (ValidationError)" in message


def test_a_proxy_that_cannot_be_built_from_the_document_says_so(issuer: Issuer) -> None:
    # A document that declares itself non-strict passes discovery without a token endpoint;
    # FastMCP's proxy then refuses it.
    issuer.left_out = {"token_endpoint"}
    issuer.extra = {"strict": False}

    with pytest.raises(InvalidConfiguration) as caught:
        _ = authenticator(http_settings(), oidc_settings(issuer.url))

    message = caught.value.describe()
    assert "could not read" not in message
    assert "could not set up the OIDC proxy from the discovery document" in message
    assert "(ValueError)" in message
    assert CLIENT_SECRET not in message


def test_a_stub_issuer_on_a_reused_port_is_not_answered_from_fastmcps_cache() -> None:
    # FastMCP keeps discovery documents by URL for five minutes per process, and the kernel hands
    # out a closed stub's port again now and then: the next test must not get its document.
    with stub_issuer() as first:
        first.left_out = {"token_endpoint"}
        first.extra = {"strict": False}
        with pytest.raises(InvalidConfiguration):
            _ = authenticator(http_settings(), oidc_settings(first.url))

    with stub_issuer(cast("int", urlsplit(first.url).port)) as second:
        assert isinstance(authenticator(http_settings(), oidc_settings(second.url)), MultiAuth)


# --- origin and storage --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "origin"),
    [
        ("https://h.example", "https://h.example"),
        ("https://h.example:443", "https://h.example"),
        ("https://h.example:80", "https://h.example:80"),
        ("https://h.example:8443", "https://h.example:8443"),
        ("http://localhost:80", "http://localhost"),
        ("http://localhost:443", "http://localhost:443"),
        ("http://localhost:8000", "http://localhost:8000"),
    ],
)
def test_the_public_origin_drops_only_the_default_port_of_its_scheme(
    base: str, origin: str
) -> None:
    oidc = OidcSettings.model_validate({"base_url": base})

    assert public_origin(oidc) == origin


@pytest.mark.anyio
@pytest.mark.parametrize("client_id", ["../../x", "a/b", "x" * 300, "..", "%2e%2e%2fx"])
async def test_odd_client_ids_get_a_clean_refusal_with_a_storage_directory(
    issuer: Issuer, tmp_path: Path, client_id: str
) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url, storage_dir=str(tmp_path)))

    async with running(app) as client:
        outcome = await authorize(client, client_id, CLAUDE_CALLBACK)

    assert outcome.startswith('400 {"error":"invalid_request"')


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["a/b", "y" * 300, "../../escape", ".."])
async def test_the_file_store_keeps_any_key_inside_its_collection(
    issuer: Issuer, tmp_path: Path, key: str
) -> None:
    directory = tmp_path / "state"
    directory.mkdir()
    store = oauth_storage(oidc_settings(issuer.url, storage_dir=str(directory)))

    await store.put(key=key, value={"v": 1}, collection="clients")
    found = await store.get(key=key, collection="clients")
    other = await store.get(key=key, collection="other")

    assert found == {"v": 1}
    assert other is None
    assert names(tmp_path) == ["state"]  # nothing was written beside the storage directory


@pytest.mark.anyio
async def test_the_storage_key_is_stretched_from_the_signing_key_like_fastmcp_does(
    issuer: Issuer, tmp_path: Path
) -> None:
    oidc = oidc_settings(issuer.url, storage_dir=str(tmp_path))
    store = oauth_storage(oidc)
    await store.put(key="k", value={"v": 1}, collection="c")

    salt = "paperless-mcp-oauth-storage"
    stretched = derive_jwt_key(low_entropy_material=SIGNING_KEY, salt=salt)
    hkdf_only = derive_jwt_key(high_entropy_material=SIGNING_KEY, salt=salt)

    assert await store.get(key="k", collection="c") == {"v": 1}
    assert await reader_like(oidc, stretched).get(key="k", collection="c") == {"v": 1}
    assert await reader_like(oidc, hkdf_only).get(key="k", collection="c") is None


def reader_like(oidc: OidcSettings, key: bytes) -> FernetEncryptionWrapper:
    """A store over the same directory, with the given Fernet key."""
    inner = oauth_storage(oidc)
    assert isinstance(inner, FernetEncryptionWrapper)
    return FernetEncryptionWrapper(
        key_value=inner.key_value, fernet=Fernet(key=key), raise_on_decryption_error=False
    )


def test_the_memory_store_is_capped_per_collection(issuer: Issuer) -> None:
    store = oauth_storage(oidc_settings(issuer.url))

    assert getattr(store, "max_entries_per_collection", None) == MEMORY_ENTRIES


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


async def signs_in(app: Starlette) -> bool:
    """Whether a FastMCP client completes the OAuth flow and lists the tools."""
    try:
        with anyio.fail_after(30):
            return "get_instance_overview" in await tool_names(app, Browser(app), {})
    except Exception:  # noqa: BLE001 -- any failure of the flow or the call counts as refused
        return False


OTHER_KEY = jwk.RSAKey.generate_key(2048, parameters={"kid": "other-key"})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("claims", "signer", "kid"),
    [
        ({"aud": "another-client"}, None, None),
        ({"iss": "https://elsewhere.example"}, None, None),
        ({"exp": 1_000_000_000, "iat": 999_996_400}, None, None),
        ({}, None, "unknown-key"),
        ({}, OTHER_KEY, "stub-key"),
    ],
    ids=["audience", "issuer", "expired", "unknown-kid", "bad-signature"],
)
async def test_an_id_token_that_fails_a_check_signs_nobody_in(
    issuer: Issuer, claims: dict[str, object], signer: jwk.RSAKey | None, kid: str | None
) -> None:
    issuer.claims, issuer.signer, issuer.kid = claims, signer, kid
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with app.router.lifespan_context(app):
        assert not await signs_in(app)


@pytest.mark.anyio
async def test_a_valid_id_token_signs_in(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with app.router.lifespan_context(app):
        assert await signs_in(app)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "authorization",
    [None, f"Bearer {BEARER_TOKEN}", "Bearer forged"],
    ids=["none", "bearer-shaped", "forged-jwt"],
)
async def test_an_oidc_only_app_refuses_anything_but_its_own_tokens(
    issuer: Issuer, authorization: str | None
) -> None:
    oidc = oidc_settings(issuer.url)
    app = make_app(http_settings(), oidc)
    if authorization == "Bearer forged":
        # Shaped like the server's own tokens, with its issuer and audience, but another key.
        now = int(time.time())
        claims = {
            "iss": BASE,
            "aud": f"{BASE}/mcp",
            "client_id": "x",
            "scope": "openid",
            "exp": now + 600,
            "iat": now,
            "jti": "forged",
        }
        forged = jwt.encode({"alg": "HS256"}, claims, jwk.OctKey.import_key(b"k" * 32))
        authorization = f"Bearer {forged}"
    headers = {"Accept": "application/json, text/event-stream"}
    if authorization is not None:
        headers["Authorization"] = authorization

    async with running(app) as client:
        response = await client.post("/mcp", json={}, headers=headers)

    assert response.status_code == 401


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


async def register(
    client: httpx2.AsyncClient, redirect_uri: str, name: str = "connector"
) -> httpx2.Response:
    return await client.post(
        "/register",
        json={
            "redirect_uris": [redirect_uri],
            "client_name": name,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )


def authorization(client_id: str, redirect_uri: str) -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": "state-1",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "code_challenge_method": "S256",
    }


async def authorize(client: httpx2.AsyncClient, client_id: str, redirect_uri: str) -> str:
    """Where an authorization request leads: the consent page, or an error (status and text)."""
    response = await client.get("/authorize", params=authorization(client_id, redirect_uri))
    if response.status_code == 302:
        return f"302 {response.headers['location']}"
    return f"{response.status_code} {response.text[:200]}"


@pytest.mark.anyio
async def test_the_claude_ai_callback_is_allowed_and_a_foreign_one_is_not(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        claude = await register(client, CLAUDE_CALLBACK)
        claude_com = await register(client, CLAUDE_COM_CALLBACK)
        foreign = await register(client, "https://evil.example/callback")
        client_id = cast("str", claude.json()["client_id"])
        consent = await authorize(client, client_id, CLAUDE_CALLBACK)
        elsewhere = await authorize(client, client_id, "https://evil.example/callback")

    assert claude.status_code == 201
    assert claude_com.status_code == 201
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


# --- the pages a browser shows -------------------------------------------------------------------

ICON = Path(mcp_server_paperless.__file__).with_name("icon.svg")


class Page(HTMLParser):
    """What a browser loads by itself when it shows a page; links and forms need a click."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.images: list[str] = []
        self.loads: list[str] = []
        self.policy: str = ""
        self.text: str = ""
        self.feed(text)
        # Style sheets, inline or in an attribute, load what url() and @import name.
        self.loads += cast("list[str]", re.findall(r"(?i)(?:url\s*\(|@import)[^;)]*", text))

    @override
    def handle_data(self, data: str) -> None:
        self.text += data

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        given = dict(attrs)
        if tag == "img":
            self.images.append(given.get("src") or "")
        loading = {"src", "srcset", "poster", "data", "background"}
        loading |= {"href", "xlink:href"} if tag not in {"a", "area"} else set()
        self.loads += [value or "" for name, value in attrs if name in loading]
        if tag == "meta" and given.get("http-equiv") == "Content-Security-Policy":
            self.policy = given.get("content") or ""
        elif tag == "meta" and given.get("http-equiv"):  # a refresh, for one, goes elsewhere
            self.loads.append(given.get("content") or "")


async def sign_in_page(client: httpx2.AsyncClient, page: str) -> httpx2.Response:
    """The named page of the sign-in, as the running app renders it."""
    html = {"Accept": "text/html"}
    if page == "unregistered client":
        query = authorization("unknown", CLAUDE_CALLBACK)
        return await client.get("/authorize", params=query, headers=html)
    if page in {"consent", "no consent given"}:
        client_id = cast("str", (await register(client, CLAUDE_CALLBACK)).json()["client_id"])
        location = (await authorize(client, client_id, CLAUDE_CALLBACK)).removeprefix("302 ")
        if page == "consent":
            return await client.get(location, headers=html)
        [txn_id] = parse_qs(urlsplit(location).query)["txn_id"]
        return await client.get("/auth/callback", params={"code": "c", "state": txn_id})
    query = {
        "callback without a code": {},
        "callback error": {"error": "access_denied", "error_description": "no"},
        "expired transaction": {"code": "c", "state": "unknown"},
    }[page]
    return await client.get("/auth/callback", params=query, headers=html)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("page", "status"),
    [
        ("consent", 200),
        ("unregistered client", 400),
        ("callback without a code", 400),
        ("callback error", 400),
        ("expired transaction", 400),
        ("no consent given", 403),
    ],
)
async def test_the_sign_in_pages_load_nothing_and_show_the_embedded_icon(
    issuer: Issuer, page: str, status: int
) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        response = await sign_in_page(client, page)

    shown = Page(response.text)
    icon = f"data:image/svg+xml;base64,{base64.b64encode(ICON.read_bytes()).decode()}"
    assert response.status_code == status
    assert shown.images == [icon]
    assert f'<img src="{icon}" alt="' in response.text  # the tag stays well formed
    assert shown.loads == [icon]
    # A Link header could make the browser load, too: it names nothing but this server.
    links = cast("list[str]", re.findall(r"<([^>]*)>", response.headers.get("link", "")))
    assert all(link.startswith(f"{BASE}/") for link in links)
    # The page's own policy lets the browser show it.
    policy = {d.split()[0]: d.split()[1:] for d in shown.policy.split(";") if d.strip()}
    assert "data:" in policy["img-src"]
    assert policy["default-src"] == ["'none'"]
    assert response.headers["content-length"] == str(len(response.content))


@pytest.mark.anyio
async def test_the_plain_consent_error_loads_nothing(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        response = await client.get("/consent")

    assert response.status_code == 400
    assert Page(response.text).loads == []


def test_the_icon_of_the_pages_is_not_sent_to_every_client(issuer: Issuer) -> None:
    oidc = oidc_settings(issuer.url)

    # FastMCP puts the server's icons into every initialize and discover answer.
    assert serve(admin(), auth=authenticator(http_settings(), oidc)).icons == []


LOGO = "https://gofastmcp.com/assets/brand/blue-logo.png"


async def consent_page(
    client: httpx2.AsyncClient, redirect_uri: str, name: str = "connector"
) -> httpx2.Response:
    registered = await register(client, redirect_uri, name)
    assert registered.status_code == 201, registered.text
    client_id = cast("str", registered.json()["client_id"])
    location = (await authorize(client, client_id, redirect_uri)).removeprefix("302 ")
    return await client.get(location)


@pytest.mark.anyio
@pytest.mark.parametrize("name", [LOGO, f'src="{LOGO}"', f'<img src="{LOGO}"'])
async def test_what_a_client_registers_is_shown_unchanged(issuer: Issuer, name: str) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))
    redirect_uri = f"http://localhost:8000/callback?next={LOGO}"

    async with running(app) as client:
        response = await consent_page(client, redirect_uri, name)

    shown = Page(response.text)
    # Twice each: in the question or the yellow box, and in the details.
    assert shown.text.count(redirect_uri) == 2
    assert shown.text.count(name) >= 2
    assert "data:image" not in shown.text
    assert len(shown.images) == 1
    assert shown.images[0].startswith("data:image/svg+xml;base64,")


@pytest.mark.anyio
async def test_the_error_of_the_identity_provider_is_shown_unchanged(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))
    query = {"error": f"denied {LOGO}", "error_description": f'see src="{LOGO}"'}

    async with running(app) as client:
        response = await client.get("/auth/callback", params=query)

    shown = Page(response.text)
    assert f'Authentication failed: see src="{LOGO}"' in shown.text
    assert f"denied {LOGO}" in shown.text
    assert "data:image" not in shown.text
    assert len(shown.images) == 1


@pytest.mark.anyio
async def test_a_name_full_of_logos_does_not_inflate_the_page(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))
    name = LOGO * 1_200  # as long as the registration may be (decision-39)

    async with running(app) as client:
        response = await consent_page(client, CLAUDE_CALLBACK, name)

    assert response.status_code == 200
    # The page shows the name twice; everything else on it is small.
    assert len(response.content) < 2 * len(name) + 20_000


@pytest.mark.anyio
async def test_the_body_limit_covers_the_registration(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        response = await register(client, CLAUDE_CALLBACK, "x" * body_limit(MAX_UPLOAD))

    assert response.status_code == 413


def test_without_the_oidc_layer_no_response_is_rewritten(issuer: Issuer) -> None:
    bearer = http_settings(auth_tokens=BEARER_TOKEN)
    server = serve(admin(), auth=authenticator(bearer))
    plain = build_app(server, bearer, max_upload_bytes=MAX_UPLOAD)
    with_oidc = make_app(http_settings(), oidc_settings(issuer.url))

    assert OwnIcon not in {entry.cls for entry in plain.user_middleware}
    assert OwnIcon in {entry.cls for entry in with_oidc.user_middleware}


@pytest.mark.anyio
@pytest.mark.parametrize("media", ["text/html; charset=utf-8", "text/event-stream"])
async def test_only_html_is_held_back_and_changed(media: str) -> None:
    logo = LOGO.encode()
    chunks = [b'<img src="' + logo[:9], logo[9:] + b'"><img src="' + logo + b'">']
    headers = [
        (b"content-type", media.encode()),
        (b"set-cookie", b"a=1"),
        (b"set-cookie", b"b=2"),
        (b"content-length", b"1"),
    ]

    async def page(scope: Scope, receive: Receive, send: Send) -> None:
        _ = scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": chunks[0], "more_body": True})
        await send({"type": "http.response.body", "body": chunks[1]})

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.request"}

    await OwnIcon(page)({"type": "http"}, receive, send)

    start, *bodies = sent
    if media == "text/event-stream":
        assert start["headers"] == headers
        assert [message["body"] for message in bodies] == chunks
    else:
        [body] = [cast("bytes", message["body"]) for message in bodies]
        assert body.startswith(b'<img src="data:image/svg+xml;base64,')
        assert body.endswith(b'"><img src="' + logo + b'">')  # only the first: FastMCP's one logo
        assert start["headers"] == [*headers[:3], (b"content-length", str(len(body)).encode())]


# --- the body limit of every route but the MCP endpoint (decision-39) ----------------------------

SMALL = 64 * 1024
FORM = {"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE}


class Upload:
    """A body without a declared length, counting what the server pulls of it."""

    def __init__(self, size: int) -> None:
        self.size: int = size
        self.pulled: int = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while self.pulled < self.size:
            chunk = min(8192, self.size - self.pulled)
            self.pulled += chunk
            yield b"x" * chunk


def served(app: Starlette) -> list[tuple[str, str]]:
    """Every method and path of the app, except the MCP endpoint."""
    return sorted(
        (method, cast("str", getattr(route, "path", "")))
        for route in app.routes
        for method in cast("set[str]", getattr(route, "methods", None) or {"POST"})
        if getattr(route, "path", "") != "/mcp"
    )


@pytest.mark.anyio
@pytest.mark.decision("decision-39")
async def test_every_oauth_route_refuses_a_declared_body_over_64_kib(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))
    routes = served(app)

    async with running(app) as client:
        over = {
            route: (await client.request(*route, content=b"x" * (SMALL + 1), headers=FORM))
            for route in routes
        }
        exact = {
            route: (await client.request(*route, content=b"x" * SMALL, headers=FORM))
            for route in routes
        }

    assert {("POST", "/register"), ("POST", "/token"), ("POST", "/consent")} <= set(routes)
    assert {route: r.status_code for route, r in over.items()} == dict.fromkeys(routes, 413)
    assert 413 not in {r.status_code for r in exact.values()}


@pytest.mark.anyio
@pytest.mark.decision("decision-39")
async def test_every_oauth_route_cuts_off_a_chunked_body_at_64_kib(
    issuer: Issuer, caplog: pytest.LogCaptureFixture
) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))
    routes = served(app)
    over: dict[tuple[str, str], int] = {}
    exact: set[int] = set()

    async with running(app) as client:
        for route in routes:
            upload = Upload(1024 * 1024)
            response = await client.request(*route, content=upload, headers=FORM)
            over[route] = response.status_code
            assert upload.pulled <= SMALL + 2 * 8192, route
        # Refused before any handler: none can catch the refusal or log a traceback of it.
        assert not [record for record in caplog.records if record.exc_info]
        for route in routes:
            response = await client.request(*route, content=Upload(SMALL), headers=FORM)
            exact.add(response.status_code)

    assert over == dict.fromkeys(routes, 413)
    assert 413 not in exact


@pytest.mark.anyio
@pytest.mark.decision("decision-39")
@pytest.mark.parametrize(
    "path", ["/revoke", "/register/", "//register", "/mcp/", "/MCP", "/%6dcp/x", "/mcp%0A"]
)
async def test_any_path_but_the_mcp_endpoint_has_the_small_limit(issuer: Issuer, path: str) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))

    async with running(app) as client:
        # As raw paths: a client that joins URLs would tidy them up.
        url = httpx2.URL(BASE).copy_with(raw_path=path.encode())
        response = await client.post(url, content=b"x" * (SMALL + 1))

    assert response.status_code == 413


@pytest.mark.anyio
@pytest.mark.decision("decision-39")
async def test_an_mcp_path_that_collides_with_an_oauth_route_does_not_raise_its_limit(
    issuer: Issuer,
) -> None:
    app = make_app(http_settings(path="/register"), oidc_settings(issuer.url))

    async with running(app) as client:
        response = await register(client, CLAUDE_CALLBACK, "x" * SMALL)

    assert response.status_code == 413


@pytest.mark.anyio
@pytest.mark.decision("decision-39")
@pytest.mark.parametrize("path", ["/mcp", "/paperless/mcp"])
async def test_the_mcp_endpoint_keeps_the_upload_limit(issuer: Issuer, path: str) -> None:
    http = http_settings(auth_tokens=BEARER_TOKEN, path=path)
    app = make_app(http, oidc_settings(issuer.url))
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    async with running(app) as client:
        fits = await client.post(path, content=b"x" * body_limit(MAX_UPLOAD), headers=headers)
        over = await client.post(path, content=b"x" * (body_limit(MAX_UPLOAD) + 1), headers=headers)

    assert fits.status_code == 400  # read, and no JSON
    assert over.status_code == 413


@pytest.mark.anyio
@pytest.mark.decision("decision-39")
async def test_a_registration_with_many_redirect_uris_passes(issuer: Issuer) -> None:
    app = make_app(http_settings(), oidc_settings(issuer.url))
    uris = [f"http://localhost:{port}/oauth/callback/of/one/client" for port in range(8000, 9000)]
    metadata = {
        "redirect_uris": uris,
        "client_name": "A client with a long name " * 4,
        "client_uri": "https://client.example.test/",
        "logo_uri": "https://client.example.test/logo.png",
        "contacts": ["someone@example.test"],
        "tos_uri": "https://client.example.test/terms",
        "policy_uri": "https://client.example.test/privacy",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "software_id": "client",
        "software_version": "1.2.3",
    }

    async with running(app) as client:
        response = await client.post("/register", json=metadata)

    assert len(response.request.content) > SMALL * 3 // 4  # close to the limit, yet within it
    assert response.status_code == 201
    assert response.json()["redirect_uris"] == uris
