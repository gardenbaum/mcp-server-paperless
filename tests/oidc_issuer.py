"""A stub OpenID Connect issuer on a loopback port, for tests of the OIDC layer (decision-31).

FastMCP's ``OIDCProxy`` reads the discovery document while it is built, exchanges codes at the
token endpoint and fetches the signing keys, each with its own HTTP client: none of them takes an
injected transport, so the stub listens on 127.0.0.1 (nothing leaves the machine). It serves the
discovery document, an authorization endpoint that approves at once, a token endpoint that checks
the client secret and PKCE, and the JWKS. Access tokens are opaque, as Google's are; the identity
is in the signed ID token.
"""

import base64
import contextlib
import hashlib
import http.server
import itertools
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, override
from urllib.parse import parse_qs, urlencode, urlsplit

from joserfc import jwk, jwt

if TYPE_CHECKING:
    from collections.abc import Generator

CLIENT_ID = "paperless-mcp-test-client"
CLIENT_SECRET = "stub-issuer-client-secret-not-a-secret"
SUBJECT = "alice"
# FastMCP caches discovery documents by URL and a port may come back within a run: a path of its
# own keeps each stub's URL unique in the process.
_NUMBERS = itertools.count(1)


@dataclass
class Issuer:
    """What the stub knows and what it was asked."""

    url: str
    key: jwk.RSAKey
    authorizations: list[dict[str, str]] = field(default_factory=list)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    codes: dict[str, dict[str, str]] = field(default_factory=dict)
    issued: list[str] = field(default_factory=list)
    # What a test changes: ID-token claims (None drops a claim), the key that signs it and the
    # kid it names, and discovery fields left out.
    claims: dict[str, object] = field(default_factory=dict)
    signer: jwk.RSAKey | None = None
    kid: str | None = None
    left_out: set[str] = field(default_factory=set)
    extra: dict[str, object] = field(default_factory=dict)

    def discovery(self) -> dict[str, object]:
        document: dict[str, object] = {
            "issuer": self.url,
            "authorization_endpoint": f"{self.url}/authorize",
            "token_endpoint": f"{self.url}/token",
            "jwks_uri": f"{self.url}/jwks",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email"],
            "code_challenge_methods_supported": ["S256"],
        }
        return {k: v for k, v in document.items() if k not in self.left_out} | self.extra

    def id_token(self) -> str:
        now = int(time.time())
        claims = {
            "iss": self.url,
            "sub": SUBJECT,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 3600,
            "email": "alice@example.test",
        } | self.claims
        claims = {k: v for k, v in claims.items() if v is not None}
        header = {"alg": "RS256", "kid": self.kid or self.key.kid}
        return jwt.encode(header, claims, self.signer or self.key)


def _pkce_matches(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=") == challenge


class _Handler(http.server.BaseHTTPRequestHandler):
    issuer: ClassVar[Issuer]

    def _send(self, status: int, body: object = None, location: str | None = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        _ = self.wfile.write(payload)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        base = urlsplit(self.issuer.url).path
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        if url.path == f"{base}/.well-known/openid-configuration":
            self._send(200, self.issuer.discovery())
        elif url.path == f"{base}/jwks":
            self._send(200, {"keys": [self.issuer.key.as_dict(private=False)]})
        elif url.path == f"{base}/authorize":
            self.issuer.authorizations.append(query)
            if query.get("client_id") != CLIENT_ID:
                self._send(400, {"error": "invalid_client"})
                return
            code = secrets.token_urlsafe(16)
            self.issuer.codes[code] = query
            target = f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"
            self._send(302, location=target)
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        self.issuer.token_requests.append(form)
        if urlsplit(self.path).path != f"{urlsplit(self.issuer.url).path}/token":
            self._send(404, {"error": "not_found"})
            return
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if self.headers.get("Authorization") != f"Basic {expected}":
            self._send(401, {"error": "invalid_client"})
            return
        grant = self.issuer.codes.pop(form.get("code", ""), None)
        if (
            form.get("grant_type") != "authorization_code"
            or grant is None
            or form.get("redirect_uri") != grant["redirect_uri"]
            or not _pkce_matches(form.get("code_verifier", ""), grant["code_challenge"])
        ):
            self._send(400, {"error": "invalid_grant"})
            return
        access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        id_token = self.issuer.id_token()
        self.issuer.issued.extend([access, refresh, id_token])
        self._send(
            200,
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": refresh,
                "id_token": id_token,
                "scope": grant.get("scope", ""),
            },
        )

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Keep the stub quiet."""


@contextlib.contextmanager
def stub_issuer(port: int = 0) -> Generator[Issuer]:
    """Serve a stub issuer on a loopback port (a free one by default) while the block runs."""
    key = jwk.RSAKey.generate_key(2048, parameters={"kid": "stub-key", "use": "sig"})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    issuer = Issuer(url=f"http://127.0.0.1:{server.server_port}/issuer-{next(_NUMBERS)}", key=key)
    server.RequestHandlerClass = type("Handler", (_Handler,), {"issuer": issuer})
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield issuer
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
