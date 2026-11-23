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
**Supersedes decision-38.** Everything in decision-38, and what it carries over from decision-27, decision-28, decision-31, decision-32, decision-33, decision-36 and decision-37, is carried over (including its consequences and enforcement). One rule changes: the body limit of decision-27, which decision-31 carried over, becomes:

- A request body over the base64 size of `PAPERLESS_MCP_MAX_UPLOAD_BYTES` plus 64 KiB gets 413 from this server at the MCP endpoint (by declared length without reading, or cut off while streaming). The MCP SDK refuses a body over 4 MiB there as well, and FastMCP leaves that at its default, so with the default upload limit its cap is the effective one and a file of at most about 3 MiB fits. Everywhere else a body over 64 KiB (65 536 bytes) gets 413: by declared length without reading, or once reading passes the limit, before any handler runs (`app.SmallBodies`, the SDK's `RequestBodyLimitMiddleware`).
- The small limit is the default and the large one the exception: it applies only to the path that equals `PAPERLESS_MCP_HTTP_PATH` exactly. There is no list of OAuth routes, so a route FastMCP adds, a trailing slash or another spelling of a path gets the small limit. If the MCP path is also the path of an OAuth route, that path gets the small limit, too.
- The limit holds with and without the OIDC layer; without it the server has no other route, so only requests that get 404 anyway are affected.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A registration of a client with about a thousand redirect URIs still fits. The limit is not configurable.
- The in-memory store is bounded in bytes only indirectly, and an entry can be larger than the body it came from, because the stored JSON escapes characters: a body of 65 KB retained 67 KB with ASCII text, 196 KB with other text such as accented letters and 390 KB in the worst case found (control characters, raw bytes). 10 000 registrations can therefore hold up to about 4 GB, and as many authorization transactions (a `state` of 64 000 bytes retained 65 to 385 KB) as much again. A storage directory stays unbounded. Rate limiting `/register` and `/authorize` at the reverse proxy (decision-31) stays necessary.
- Off the MCP endpoint the body, at most 64 KiB, is read before the host and origin guard answers.
- The query string and headers are not part of the body; this limit does not bound them.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/test_oidc.py` (marker decision-39): every method and path of the running app except the MCP endpoint, read from its routes, refuses a declared body of 64 KiB + 1 and takes one of 64 KiB, and answers a chunked one over the limit with 413 after reading no more than the limit and a chunk or two, without a traceback in the log; unknown paths, trailing slashes and other spellings of the MCP path get the small limit; so does an MCP path that collides with `/register`; the MCP endpoint keeps the upload limit on the default and on another path; a registration with a thousand redirect URIs passes.
- `tests/test_app.py` (marker decision-27): this server's own body limit of the MCP endpoint.
- Everything decision-38 lists under Enforcement still runs.
