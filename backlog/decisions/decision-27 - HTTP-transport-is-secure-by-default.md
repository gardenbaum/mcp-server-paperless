---
id: decision-27
title: HTTP transport is secure by default
date: '2026-11-17T01:28:00Z'
status: accepted
---
## Context

Decision-9 fixed the shape of the HTTP transport (secure by default, static bearer tokens first, a host allowlist and a request id). PLMCP-34 builds it and has to settle what "secure by default" means in detail: what counts as loopback, how tokens are configured and compared, which routes exist, and what bounds a request before the tools see it. An open MCP endpoint is a remote Paperless account for anyone who can reach it.

## Decision

- Entrypoint: `paperless-mcp-http` (`paperless_mcp.cli:main_http`, `just serve-http`); `app.create_app` builds the ASGI app. stdio stays `paperless-mcp` and is unchanged. The HTTP server is always created with `stdio=False`.
- Startup refusal (`app.check_startup`): without `PAPERLESS_MCP_HTTP_AUTH_TOKENS` the server does not start, unless the bind is loopback and `PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK=true`. Every other bind is remote: 0.0.0.0, `::`, the empty host, any other address, and any host name unless every address it resolves to is loopback (a name that does not resolve is remote). IPv4-mapped IPv6 addresses are judged by the IPv4 address inside. A remote bind also needs `PAPERLESS_MCP_HTTP_ALLOWED_HOSTS`.
- Tokens: a list in `PAPERLESS_MCP_HTTP_AUTH_TOKENS` (comma-separated or JSON array, like the other list settings), held as `SecretStr` like `PAPERLESS_TOKEN`, at least 16 characters of RFC 6750 token characters. Verification (`StaticBearerVerifier`) keeps only SHA-256 digests and compares the presented token's digest with every configured digest through `hmac.compare_digest`, without early exit. The `AccessToken` carries a label (`static-token-N`), never the token. A token in the query string is not a credential. Requests without a valid token get 401 on every method.
- Hosts and origins: FastMCP's `HostOriginGuard` in strict mode (not Starlette's `TrustedHostMiddleware`, which mis-parses IPv6 Host headers and does not look at Origin). Allowed: the loopback names plus `PAPERLESS_MCP_HTTP_ALLOWED_HOSTS` (plain names, no wildcards). A foreign Host gets 421, a foreign browser Origin 403, both before authentication. No CORS is configured: browsers get no cross-origin access.
- Defence in depth: while no token is configured the app answers only when the server address of the connection is loopback (403 otherwise), so uvicorn started by hand with another `--host` stays closed.
- Routes: only the MCP endpoint (`PAPERLESS_MCP_HTTP_PATH`, default `/mcp`). No health endpoint, no metadata routes; everything else is 404. A health endpoint needs its own decision (PLMCP-35 may ask for one).
- Request id and log: every response carries `X-Request-ID` (the caller's if short and made of `A-Za-z0-9._-`, else generated), outermost, so refusals have one too. One `http request` record per request with method, path, status and duration; never query, headers, tokens or bodies. Tool-call records carry the id of their HTTP request. uvicorn's access log is off and it does not trust forwarded headers.
- Body limit: a request body over the base64 size of `PAPERLESS_MCP_MAX_UPLOAD_BYTES` plus 64 KiB gets 413: by declared length without reading, or cut off while streaming.
- Transport rule of decision-7 holds: the HTTP app registers no tool with a path parameter, whatever `PAPERLESS_MCP_UPLOAD_DIRS` says.

## Consequences

- Running the server remotely means setting tokens and hosts; the error names the variable to set.
- The unauthenticated mode is for local development behind the loopback interface only; any local process can use it.
- Tokens are static and shared; rotation means restarting. Rate limiting of failed attempts and TLS belong to a reverse proxy. Per-user identity arrives with PLMCP-36 and PLMCP-37 and replaces the verifier at the composition root.
- Hosts behind a proxy must list the host name the proxy forwards.

## Enforcement

- `tests/test_app.py` (marker decision-27): loopback classification (IPv4, IPv6, IPv4-mapped, names, unresolvable), the startup refusals, 401 for missing, wrong and malformed credentials on every method, Host and Origin guards, the single route, request ids and logs without secrets, the body limit and the loopback-only guard.
- `tests/test_settings.py`: token strength and format, host and path validation, tokens masked in `repr`.
- `tests/test_cli.py`: the entrypoint refuses without authentication and never echoes invalid values.
- `tests/tools/test_registry.py`: the HTTP entrypoint offers base64 uploads and no path parameter (a registry test per transport next to the stdio one).
