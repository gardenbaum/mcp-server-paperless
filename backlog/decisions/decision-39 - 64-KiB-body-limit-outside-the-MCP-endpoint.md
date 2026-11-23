---
id: decision-39
title: 64 KiB body limit outside the MCP endpoint
date: '2026-11-23T13:19:28Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Decision-27 bounds every request body by the upload limit (14 046 552 bytes at the defaults). With the OIDC layer of decision-31 the same bound applied to the OAuth routes, which need no authentication: a registration of that size is stored (in memory at most 10 000 entries without a byte cap, on disk without any), and its consent page grew to 28 MB (found while fixing PLMCP-92). The MCP SDK wraps its auth routes in a limit of 4 MiB (`_body_limited` in `mcp/server/auth/routes.py`), but FastMCP 4.2 replaces `/register`, `/token` and `/authorize` with handlers of its own without that wrapper, and `/consent` is FastMCP's own: `/register` read a body of 5 MiB to its end when the app ran without this server's limit. The owner approved a limit of 64 KiB for these routes on 2026-11-23.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes decision-38.** Everything in decision-38, and what it carries over from decision-27, decision-28, decision-31, decision-32, decision-33, decision-36 and decision-37, is carried over unchanged (including its consequences and enforcement), except that the body limit of decision-27 (carried over by decision-31) becomes:

- A request body over the base64 size of `PAPERLESS_MCP_MAX_UPLOAD_BYTES` plus 64 KiB gets 413 at the MCP endpoint, as before. Everywhere else a body over 64 KiB (65 536 bytes) gets 413. Both by declared length without reading, or cut off while streaming.
- The small limit is the default and the large one the exception: it applies only to the path that equals `PAPERLESS_MCP_HTTP_PATH` exactly. There is no list of OAuth routes, so a route FastMCP adds, a trailing slash or another spelling of a path gets the small limit. If the MCP path is also the path of an OAuth route, that path gets the small limit, too.
- The limit holds with and without the OIDC layer; without it the server has no other route, so only requests that get 404 anyway are affected.
- A handler that catches the error of a body cut off while streaming, as FastMCP's `/authorize` does, does not decide the answer: it is 413 as long as no response has begun.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A registration of a client with about a thousand redirect URIs still fits. The limit is not configurable.
- The in-memory store is bounded in bytes only indirectly: 10 000 registrations of at most 64 KiB are still about 650 MB, and authorization transactions come on top. A storage directory stays unbounded. Rate limiting `/register` and `/authorize` at the reverse proxy (decision-31) stays necessary.
- The query string and headers are not part of the body; uvicorn bounds them.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/test_oidc.py` (marker decision-39): every method and path of the running app except the MCP endpoint, read from its routes, refuses a declared body of 64 KiB + 1 and takes one of 64 KiB, and stops reading a chunked one at the limit, with 413 from every POST route; unknown paths, trailing slashes and other spellings of the MCP path get the small limit; so does an MCP path that collides with `/register`; the MCP endpoint keeps the upload limit on the default and on another path; a registration with a thousand redirect URIs passes.
- `tests/test_app.py` (marker decision-27): the body limit of the MCP endpoint, unchanged.
- Everything decision-38 lists under Enforcement still runs unchanged.
