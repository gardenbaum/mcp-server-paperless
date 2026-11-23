---
updated_date: '2026-11-23T14:19:10Z'
id: decision-40
title: Raise the MCP SDK's request body limit on the running app
date: '2026-11-23T14:15:57Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The MCP SDK (2.3.0) refuses bodies over 4 MiB at the MCP endpoint. Its session manager takes `max_request_body_size`, but FastMCP 4.2 neither passes it nor has an option for it (checked: 4.2.1, 4.2.5 and the main branch on 2026-11-23). So over HTTP no file over about 3 MiB can be uploaded, whatever `PAPERLESS_MCP_MAX_UPLOAD_BYTES` says (default 10 MiB). FastMCP's upgrade guide lists the parameter as "no equivalent"; PrefectHQ/fastmcp#854 (open, no pull request) reports the 413; the parameter is asked for in its comments, also by this project (https://github.com/PrefectHQ/fastmcp/issues/854#issuecomment-7104191663). The owner approved raising the limit on the running app, and keeping the 100 MiB maximum, on 2026-11-23.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes decision-39.** Everything in decision-39, and what it carries over from decision-27, decision-28, decision-31, decision-32, decision-33, decision-36, decision-37 and decision-38, is carried over (including its consequences and enforcement), except its statement that the SDK's 4 MiB cap is the effective limit of the MCP endpoint:

- At each start of the app `app.raise_sdk_limit` sets `max_body_size` of the SDK's `RequestBodyLimitMiddleware` in this app's own session manager to the limit of `app.BodyLimit` (the base64 size of `PAPERLESS_MCP_MAX_UPLOAD_BYTES` plus 64 KiB). It reaches it through public but undocumented attributes (`StreamableHTTPASGIApp.session_manager`, `asgi_app`). No code of FastMCP or the SDK is replaced, and no other app in the process changes.
- The server does not start when that limit is not where it is expected; it never serves with another limit silently.
- `app.BodyLimit` answers 413 with a JSON-RPC error that names the limit, because the SDK's client shows nothing of any other body.
- This is an override of FastMCP, not an option of it. It goes away once FastMCP offers the parameter: then this server passes the parameter instead.
- The range of `PAPERLESS_MCP_MAX_UPLOAD_BYTES` (up to 100 MiB) does not change.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A FastMCP or SDK update can move the attribute: then the server does not start, and the tests fail first.
- Memory: peaks of single runs on macOS with a stand-in for Paperless, above about 125 MB when idle: 67 MB for one 10 MiB file (three runs), 143 MB for four at once, 636 MB for a 100 MiB file on a fresh server and 845 to about 890 MB after an earlier upload of 40 MiB (two measurements). Nothing limits how many run at once.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Our own app with the SDK's session manager: needs the private `FastMCP._mcp_server` and a copy of FastMCP's app builder.
- Replacing FastMCP's manager class or the SDK's constant: monkeypatching, which this repository bans.
- Keep the cap, document it and ask FastMCP for the parameter: HTTP uploads stay at about 3 MiB.
<!-- SECTION:ALTERNATIVES:END -->


## Enforcement

- `tests/test_http_uploads.py` (marker decision-40): on a real uvicorn server, with and without a bearer token, at the default and at 20 MiB, a file of exactly the maximum size reaches the tool and one byte more gets the tool's own refusal; a larger request gets the JSON-RPC message; without a token 401 for a body of exactly the limit and 413 for one byte more; two starts of one app; the value set; an app whose limit is missing, moved, renamed or on a foreign object is not built or does not start, also with FastMCP's own session manager and under uvicorn (exit status 3, no socket).
- `tests/test_app.py` (marker decision-27): the 413 of `BodyLimit` reaches an MCP client with its message, with and without a declared length.
- Everything decision-39 lists under Enforcement still runs.
