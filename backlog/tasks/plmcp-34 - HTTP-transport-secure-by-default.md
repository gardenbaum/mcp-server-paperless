---
id: PLMCP-34
title: HTTP transport secure by default
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-17T01:47:00Z'
labels: []
milestone: m-5
dependencies:
  - PLMCP-18
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 34000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Remote use starts with an HTTP endpoint that cannot be opened by accident: without auth configuration it must not start, except on loopback with an explicit flag (design §5.7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Streamable HTTP app (ASGI) refuses to start without auth configuration unless bound to loopback with allow_unauthenticated_loopback=true
- [x] #2 Static bearer tokens protect the MCP endpoint; requests without a valid token get 401
- [x] #3 Trusted-host allowlist and request-id middleware are active
- [x] #4 In HTTP mode uploads accept base64 only and no tool exposes a filesystem path parameter (registry test per transport)
- [x] #5 README documents running over HTTP
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Settings: HttpSettings (PAPERLESS_MCP_HTTP_*: host, port, path, auth_tokens as SecretStr tuple, allowed_hosts, allow_unauthenticated_loopback), loopback classification (IPv4, IPv6, IPv4-mapped, resolved hostnames).
2. app.py: create_app() refuses to start without tokens unless loopback + explicit flag; static bearer TokenVerifier with sha256 + hmac.compare_digest over all tokens; FastMCP host/origin guard in strict mode with an allowlist; request-id + request log middleware; request body cap derived from max_upload_bytes; defence in depth: an unauthenticated app answers only on a loopback server address.
3. server.py unchanged except stdio=False for HTTP; CallLogging picks up the HTTP request id.
4. cli: 'paperless-mcp http' subcommand (stdio stays the default and unchanged); justfile recipe 'serve-http'.
5. Registry tests per transport (http, stdio): no path parameter, base64-only upload.
6. README section, decision for the HTTP rules, optional e2e smoke over real uvicorn.
Strict TDD: each test red first, WIP commits often.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-18 review: the stdio-only path variant of upload_document (tools/upload_stdio.py) must never be registered for HTTP: create_server(stdio=...) is the only switch and the CLI passes transport="stdio" explicitly (FASTMCP_TRANSPORT is ignored); the tool also refuses a path when ctx.transport is sse/streamable-http. HTTP entrypoints must call create_server with stdio=False. HTTP also needs a request-body size cap in front of the app: base64 content is parsed by FastMCP/pydantic before UploadService checks its size (the service only bounds the decode, not the parse), so an unbounded body costs memory first.

Implemented. HTTP entrypoint paperless-mcp-http (cli.main_http, just serve-http); app.create_app/build_app/check_startup; settings.HttpSettings (PAPERLESS_MCP_HTTP_*); decision-27 records the rules. Design deviations: FastMCP HostOriginGuard (strict) instead of Starlette TrustedHostMiddleware (IPv6 Host parsing, Origin check); starlette and uvicorn became direct dependencies and deptry DEP002 ignores starlette (type-only imports) - a deliberate, reasoned entry in pyproject.toml. No health endpoint (decision-27). Body cap = base64 size of max_upload_bytes + 64 KiB, checked by Content-Length and while streaming. Unauthenticated loopback mode also answers only when the connection's server address is loopback. Not written red first: tests that pin behaviour FastMCP already provides (401 for every method, Host 421, Origin 403, 404 for other routes, no CORS, registry test per transport); they were mutation-checked by hand (host_origin_protection off, stdio=True, body limit removed all fail). Mutation testing: no module under services/ or paperless/ changed, so nothing to run (mutmut covers only those). E2E: tests/e2e/test_http.py run alone under flock, 2 passed. Known limit: a Paperless that rejects the token at startup shows uvicorn's lifespan error as a JSON log line with exit code 1, not the one-line message of stdio.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the secure-by-default HTTP transport: paperless-mcp-http (just serve-http) builds an ASGI app that refuses to start without PAPERLESS_MCP_HTTP_AUTH_TOKENS unless bound to loopback with the explicit flag (IPv4, IPv6, IPv4-mapped and resolved names classified; every other bind is remote and also needs allowed hosts). Static bearer tokens are checked via SHA-256 digests and hmac.compare_digest over all tokens; every method on the single route gets 401 without a valid token. FastMCP's strict host/origin guard, an outermost request-id and request-log middleware, a body cap derived from max_upload_bytes and a loopback-only guard for the unauthenticated mode are active. The HTTP app is built with stdio=False and a registry test per transport shows base64 uploads and no path parameter. README documents HTTP; decision-27 records the rules. Verification: just check green (100 % diff coverage), real-process test and e2e test (tests/e2e/test_http.py under flock). DoD 1 left unchecked: some pins on FastMCP-provided behaviour were not written red first (mutation-checked by hand). DoD 2 and 4 await CI and merge. Follow-ups: one-line startup error for a rejected Paperless token over HTTP; optional health endpoint needs a decision (PLMCP-35).
<!-- SECTION:FINAL_SUMMARY:END -->
