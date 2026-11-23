# Run over HTTP

stdio is the default and needs nothing else. To serve several clients, or a connector, over
Streamable HTTP, run `mcp-server-paperless-http`. It **refuses to start without authentication**.

## Start it

```sh
export PAPERLESS_URL=https://paperless.example.org
export PAPERLESS_TOKEN=your-api-token
export PAPERLESS_MCP_HTTP_AUTH_TOKENS="$(openssl rand -hex 32)"   # what clients send as a bearer token
uvx --from mcp-server-paperless@latest mcp-server-paperless-http   # http://127.0.0.1:8000/mcp
claude mcp add --transport http paperless http://127.0.0.1:8000/mcp \
    --header "Authorization: Bearer $PAPERLESS_MCP_HTTP_AUTH_TOKENS"
```

From a clone, `just serve-http` or `.venv/bin/mcp-server-paperless-http` start the same server. For
a container see [Run in a container](container.md).

> [!WARNING]
> The package is `mcp-server-paperless`. Once it is installed, the old command names
> `paperless-mcp` and `paperless-mcp-http` still work, but they are not package names of this
> project. Do not run `uvx paperless-mcp` or `uvx paperless-mcp-http`: uvx takes the command for a
> PyPI project, and [`paperless-mcp`](https://pypi.org/project/paperless-mcp/) is a different server
> by someone else that would get your Paperless token.

## Authentication and allowed hosts

Requests without a valid token get 401 (GET, POST, DELETE and HEAD on the one route the server
has; other methods get 405 before authentication). Tokens are compared in constant time, are
masked in settings and never logged.

For a bind that is not loopback (`0.0.0.0`, `::`, a LAN address, any host name that does not
resolve to loopback only) also name the host names clients use. Any other `Host` header gets 421
and any foreign browser `Origin` 403, before authentication, and no CORS access is granted:

```sh
PAPERLESS_MCP_HTTP_HOST=0.0.0.0 \
PAPERLESS_MCP_HTTP_ALLOWED_HOSTS=mcp.example.org \
PAPERLESS_MCP_HTTP_AUTH_TOKENS=... mcp-server-paperless-http
```

The server speaks plain HTTP: with tokens configured, put TLS, rate limiting of failed attempts
and public exposure in a reverse proxy, and list the host name the proxy forwards in
`PAPERLESS_MCP_HTTP_ALLOWED_HOSTS`.

A call that waits for your answer in a [confirmation dialog](configuration.md#confirmation) can
stay open for up to 10 minutes on MCP versions up to 2025-11-25. The server sends a ping every 15
seconds, so a proxy's read timeout has to be longer than that, and the proxy must not buffer the
response. If the connection drops, an answer given afterwards changes nothing. Run one server
process per address: sessions and dialogs are not shared between processes.

## Without tokens, for local development only

`PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK=true` serves without tokens on a loopback
bind (and the app then answers on loopback addresses only); any process on the machine can use
it. Never put this mode behind a reverse proxy, where every client would look like loopback: it
refuses requests that carry `Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host` or `X-Real-IP`.

## Variables

| Variable | Meaning |
|---|---|
| `PAPERLESS_MCP_HTTP_HOST` / `PAPERLESS_MCP_HTTP_PORT` | Address to bind (default `127.0.0.1`, `8000`) |
| `PAPERLESS_MCP_HTTP_PATH` | Path of the endpoint (default `/mcp`); the only route, no health endpoint |
| `PAPERLESS_MCP_HTTP_AUTH_TOKENS` | Static bearer tokens, comma-separated or a JSON array, each at least 16 characters of `A-Za-z0-9._~+/=-`; several let clients switch to a new token before the old one is removed (changing the list needs a restart). Required unless the flag below is set or the [optional OIDC layer](claude-ai-connector.md) is on |
| `PAPERLESS_MCP_HTTP_ALLOWED_HOSTS` | Host names clients use, plain names without wildcards or ports; `localhost`, `127.0.0.1` and `::1` are always allowed. Required unless the bind is loopback |
| `PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK` | `true` to serve without tokens, only on a loopback bind (default `false`) |

Every other [setting](configuration.md) works the same over HTTP.

## Uploads, request ids and logs

Over HTTP callers upload by sending base64 content: no tool takes a server-side file path,
whatever `PAPERLESS_MCP_UPLOAD_DIRS` says, and none saves a file, whatever
`PAPERLESS_MCP_DOWNLOAD_DIRS` says (larger files are
[read in parts](configuration.md#uploads-and-downloads)). Request bodies larger than the base64 size of
`PAPERLESS_MCP_MAX_UPLOAD_BYTES` plus 64 KiB get 413. An MCP client is told the
limit in bytes. Off the MCP endpoint, so on every route of the
[OIDC layer](claude-ai-connector.md), the limit is 64 KiB. Every response carries an `X-Request-ID`
(yours if it is short and made of letters, digits, `.`, `_` or `-`); one JSON log line per request
(method, path, status, duration) goes to stderr, without query strings, headers or bodies. The
rules are decision-27, carried over into decision-40 (`backlog/decisions/`). What the operator of a
deployment sees is in the [privacy policy](../PRIVACY.md#if-someone-else-runs-the-server).

## Sign in through an identity provider

claude.ai custom connectors sign in with OAuth instead of a bearer token. The optional OIDC layer
for them is described in [Connect claude.ai](claude-ai-connector.md).
