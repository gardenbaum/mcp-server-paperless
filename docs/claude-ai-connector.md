# Connect claude.ai: sign in through your identity provider

claude.ai custom connectors sign in with OAuth, not with a bearer token. For them the
[HTTP server](http.md) has an **optional OIDC layer** on top of the bearer tokens: FastMCP's
generic OIDC proxy in front of any OpenID Connect provider (Authentik, Keycloak, Google, Auth0,
...), configured only through environment variables. Without these variables nothing changes. With them, claude.ai
signs in through your provider while Claude Code keeps using its bearer token on the same server;
every client still works with the one Paperless token of `PAPERLESS_TOKEN` (per-user Paperless
credentials are not supported yet). The server shows its own consent page before it sends the
browser to the provider, forwards PKCE, verifies the provider's signed ID token and issues its own
tokens to clients. The rules are decision-31 (`backlog/decisions/`).

## Variables

| Variable | Meaning |
|---|---|
| `PAPERLESS_MCP_OIDC_CONFIG_URL` | The provider's discovery document, `https://.../.well-known/openid-configuration` (required; https, plain http only on loopback). It is read at every start: a provider that cannot be reached stops the start |
| `PAPERLESS_MCP_OIDC_CLIENT_ID` / `PAPERLESS_MCP_OIDC_CLIENT_SECRET` | The confidential client you register for this server at the provider (required; the secret is masked and never logged) |
| `PAPERLESS_MCP_OIDC_BASE_URL` | The public origin of this server, e.g. `https://mcp.example.org`, without path (required; https, plain http only on loopback). Its host must be in `PAPERLESS_MCP_HTTP_ALLOWED_HOSTS`. Register `<base URL>/auth/callback` as the redirect URI at the provider |
| `PAPERLESS_MCP_OIDC_JWT_SIGNING_KEY` | At least 32 characters (`openssl rand -hex 32`, required): signs the tokens the server issues and encrypts its OAuth state. Changing it signs every OIDC client out |
| `PAPERLESS_MCP_OIDC_STORAGE_DIR` | Absolute path of a writable directory for client registrations and sessions, encrypted. Unset (default): kept in memory, so after a restart claude.ai has to connect again (bearer clients are not affected) |
| `PAPERLESS_MCP_OIDC_ALLOWED_REDIRECT_URIS` | Callbacks OAuth clients may register, comma-separated or a JSON array: https URLs or loopback http, a `*` only for the port or in the path. Default `https://claude.ai/api/mcp/auth_callback,https://claude.com/api/mcp/auth_callback,http://localhost:*,http://127.0.0.1:*` (claude.ai under either of Anthropic's domains, and clients on your machine) |

## What the layer adds, and what to watch

If some but not all of the five required variables are set, the server refuses to start and
names the missing ones (unset all `PAPERLESS_MCP_OIDC_*` variables to go back to bearer tokens
alone). With the layer on, the server adds the OAuth routes FastMCP needs and nothing else:
`/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/mcp`,
`/authorize`, `/token`, `/register`, `/consent` and `/auth/callback`. Browsers from other origins
still get 403, and the server needs TLS from your reverse proxy, which must pass the public
`Host` through. Anyone the provider lets sign in to this application uses your Paperless token:
restrict the application to yourself (or a group) at the provider.

`/register` and `/authorize` answer without authentication and each call stores an entry. In
memory at most 10 000 entries per kind are kept (the oldest are dropped, so a flood can make
claude.ai register again); in `PAPERLESS_MCP_OIDC_STORAGE_DIR` nothing bounds them. Rate-limit
`/register` and `/authorize` in your reverse proxy. FastMCP logs the provider's subject (your
user id there) when it rejects a token, so the log can hold that identifier. The server re-checks
the provider's ID token on every request; when it expires (often after an hour) it refreshes at
the provider, and if the provider returns no new ID token on refresh, claude.ai has to sign in
again.

## Example with Authentik

Any OIDC provider works the same way.

1. In Authentik, under Applications, Providers, create an *OAuth2/OpenID Provider*: client type
   *Confidential*, redirect URI `https://mcp.example.org/auth/callback` (strict), and a
   *Signing Key* (for example the self-signed certificate Authentik ships: the server checks ID
   tokens against the provider's published keys, so they must be signed with RS256, not with the
   client secret). The default scopes (`openid`, `email`, `profile`) are enough.
2. Create an *Application* for it (slug `mcp-server-paperless`) and bind it to the users or the group
   who may use your Paperless through claude.ai. Note the client id and secret.
3. Start the server with the OIDC variables next to the HTTP ones:

   ```sh
   export PAPERLESS_MCP_HTTP_HOST=0.0.0.0
   export PAPERLESS_MCP_HTTP_ALLOWED_HOSTS=mcp.example.org
   export PAPERLESS_MCP_HTTP_AUTH_TOKENS="$(openssl rand -hex 32)"          # optional: for Claude Code
   export PAPERLESS_MCP_OIDC_CONFIG_URL=https://authentik.example.org/application/o/mcp-server-paperless/.well-known/openid-configuration
   export PAPERLESS_MCP_OIDC_CLIENT_ID=the-client-id
   export PAPERLESS_MCP_OIDC_CLIENT_SECRET=the-client-secret
   export PAPERLESS_MCP_OIDC_BASE_URL=https://mcp.example.org
   export PAPERLESS_MCP_OIDC_JWT_SIGNING_KEY="$(openssl rand -hex 32)"     # keep it: changing it signs clients out
   export PAPERLESS_MCP_OIDC_STORAGE_DIR=/var/lib/paperless-mcp            # optional, survives restarts
   mcp-server-paperless-http
   ```

   For Google, Keycloak or Auth0 only the discovery URL and the redirect URI registration
   differ (Google: `https://accounts.google.com/.well-known/openid-configuration`, a *Web
   application* OAuth client in the Google Cloud console).

## Add the connector in claude.ai

In claude.ai open Settings, Connectors, and add a custom connector with the
URL `https://mcp.example.org/mcp`. Leave the advanced OAuth client id and secret empty: claude.ai
registers itself with the server. Connect, approve the server's consent page ("Allow Access"),
then sign in at your provider; claude.ai returns to its callback
(`https://claude.ai/api/mcp/auth_callback` or `https://claude.com/api/mcp/auth_callback`; either
may be used and both are allowed by default) and lists the tools. claude.ai registers and
exchanges the code from its servers, not from your browser, so those requests carry no browser
`Origin`; a 403 while connecting points to an origin problem (a proxy adding an `Origin`, or a
base URL that differs from the address you open). Claude Code
keeps working with `--header "Authorization: Bearer ..."` as in [Run over HTTP](http.md); it may also sign in through
OIDC instead (its loopback callback is allowed by default).

## In a container

The compose additions for the OIDC layer (its variables and a volume for its state) are in
[Run in a container](container.md#with-the-oidc-layer).
