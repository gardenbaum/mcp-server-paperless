---
id: PLMCP-94
title: Limit the request body of the OAuth routes
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T13:16:43Z'
updated_date: '2026-11-23T13:55:46Z'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 74000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
With OIDC, the unauthenticated OAuth routes accept bodies up to the upload limit plus overhead (14,046,552 bytes at the defaults). A registration is stored (in memory at most 10,000 entries without a byte cap, on disk without any), and its consent page grows to 28 MB (found while fixing PLMCP-92). Owner approved a limit of 64 KiB on 2026-11-23, before 0.3.3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The OAuth routes refuse bodies over 64 KiB with 413, with and without Content-Length, chunked too; other routes keep their limit
- [x] #2 The route list is derived from the app or guarded, so a new OAuth route cannot slip past it
- [x] #3 A real registration, also of a client with many redirect URIs, still passes
- [x] #4 A decision records the limit and how it supplements decision-27 and decision-31; docs/http.md or the connector page states it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red tests over every route of the running OIDC app: declared and chunked bodies, exact boundary, an unknown route, a path set to an OAuth route, real registrations, the MCP endpoint's own limit.
2. BodyLimit becomes deny by default: 64 KiB for every path except exactly the MCP endpoint, so no route list exists to forget. A handler that swallows the overrun must still answer 413.
3. New decision superseding decision-38 (current carrier of 27, 28 and 31), docs/http.md, docs/claude-ai-connector.md, carrier references.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
BodyLimit is deny by default: 64 KiB on every path except exactly the MCP path, so no list of routes exists; an MCP path colliding with another route also gets the small limit. FastMCP's POST /authorize swallowed the cut-off error, so BodyLimit now sends 413 itself while no response has begun. Decision-39 supersedes decision-38 and carries everything else over; carrier references updated. PLMCP-92's page-size test now uses a 58 KB name, because the former 960 KB name gets 413. Also asserts the exact icon tag (PLMCP-92 re-review).
Found, not changed: the MCP SDK limits the MCP endpoint itself to 4 MiB (StreamableHTTPSessionManager default, which FastMCP does not set), so HTTP uploads over about 3 MiB get 413 whatever PAPERLESS_MCP_MAX_UPLOAD_BYTES says. Needs its own task.

Review fixes, replacing parts of the notes above: BodyLimit is back to its form before this task. SmallBodies puts the MCP SDK's RequestBodyLimitMiddleware (64 KiB) in front of every path but exactly the MCP path; it refuses before any handler runs, so nothing can swallow the 413 and the SDK no longer logs a traceback for an oversized chunked POST /authorize (tested). Trade-off: off the MCP endpoint up to 64 KiB is read before the host and origin guard answers, and the 413 text is the SDK's. Memory figures in decision-39 re-measured (tracemalloc): 65 KB body retains 67 KB (ASCII), 196 KB (accented) or 390 KB (DEL characters), a transaction 65 to 385 KB. docs/http.md, docs/configuration.md and decision-39 state the 4 MiB SDK cap over HTTP; the '10 MiB by default' tool descriptions in tools/upload.py and tools/versions.py are for the follow-up.

Review M3: a handler that wraps the cut-off error gives 500 instead of 413; moot with SmallBodies, which reads before any handler. M5: run outside uvicorn_server with a root_path, the MCP path as routed differs from scope path, so /mcp then gets the small limit (fails safe).

Re-review of the redesign: 0 Critical, 0 Important; N1 closed by a test (the mutant scope["path"] fails), N2 added to decision-39. The plan's step 2 and the first notes paragraph describe the abandoned design. N3 (disconnect noise) was there before. The 4 MiB cap on /mcp waits for the owner's decision on its own task.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
SmallBodies puts the MCP SDK's body limit (64 KiB) in front of every path except exactly the MCP path, before any handler. decision-39 supersedes decision-38. The docs state the SDK's 4 MiB cap on HTTP uploads. DoD 2: one known macOS-only local failure, CI green.
<!-- SECTION:FINAL_SUMMARY:END -->
