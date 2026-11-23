---
id: PLMCP-95
title: Let HTTP uploads use the configured size limit
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T14:05:31Z'
updated_date: '2026-11-23T14:59:13Z'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 75000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The MCP SDK limits the body of the MCP endpoint to 4 MiB and FastMCP passes no other value, so uploads over about 3 MiB fail over HTTP with a bare error, whatever PAPERLESS_MCP_MAX_UPLOAD_BYTES says (default 10 MiB). Confirmed on real uvicorn by the review of PLMCP-94. The docs state the cap since then; the tool descriptions in tools/upload.py and tools/versions.py still say 10 MiB. Owner approved the fix on 2026-11-23, before 0.3.3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Over HTTP an upload up to PAPERLESS_MCP_MAX_UPLOAD_BYTES reaches the tool, at the default and at a larger setting, proven on a real server
- [x] #2 A body over this server's limit still gets 413 and nothing is read before authentication
- [x] #3 It uses a supported way of FastMCP or the SDK, or a decision says why not and what breaks it; a dependency update that brings the cap back fails a test
- [x] #4 A client gets an error it can act on when a file is too large
- [x] #5 docs, decision-39 and the tool descriptions tell the truth for stdio and HTTP
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
1. No supported way exists in FastMCP 4.2.1, 4.2.5 or main; the owner approved setting the SDK middleware's limit on this app's instance at startup.
2. app.raise_sdk_limit, called by build_app; the app is not built or does not start when the limit is not where expected.
3. JSON-RPC 413 of BodyLimit (separate commit).
4. Real uvicorn tests above 4 MiB with and without a token; decision-40; docs and memory figures.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
No supported way to raise the SDK's 4 MiB cap: FastMCP 4.2.1, 4.2.5 (latest) and its main branch pass no max_request_body_size to the SDK's session manager (mcp 2.3.0 takes it), and there is no setting for it. The owner approved option 1 (set max_body_size of this app's own SDK middleware at each start) and the unchanged 100 MiB maximum (2026-11-23).
BodyLimit's 413 is a JSON-RPC error naming the limit in bytes and the setting; before, the client showed only 'Server returned an error response'. The tool descriptions needed no change: 10 MiB by default is now true over HTTP too. The documented 'three times the file' was wrong; measured peaks for 100 MiB are 640 MB on a fresh server and 845 to about 890 MB after an earlier upload of 40 MiB.
PrefectHQ/fastmcp#854 reports the 413 (its own text does not name the parameter); a comment there asks for the parameter, and the owner-approved comment of this project is https://github.com/PrefectHQ/fastmcp/issues/854#issuecomment-7104191663.
Review of PR 100 (6 Minor, all fixed): two starts of one app, the exact value set, foreign limiter object and FastMCP's real session manager tested; refusals are InvalidConfiguration, so a build-time one is one line of main_http instead of a traceback, while a start-time one comes out of the task group as an ExceptionGroup and uvicorn exits with status 3; the 413 text holds for every request and at the maximum; 401 tested at exactly the limit. Left for the owner: services/upload_source.py has the same 'the operator can raise' wording.
CI on 57b61aa (before these fixes): check 3.14 and 3.15, audit, e2e 1-3 and the container builds green, Codex clean; it did not run on the fixes.

Delta review of 082fd86: no Critical or Important finding; two wording Minors fixed afterwards. CI on 082fd86 green (check 3.14 and 3.15, audit, e2e, container builds), Codex clean. Two hand mutations survive without practical effect: max(limit, 4 MiB), because BodyLimit bounds requests, and error code -32603.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Over HTTP, files up to PAPERLESS_MCP_MAX_UPLOAD_BYTES reach the upload tools: app.raise_sdk_limit sets the MCP SDK's 4 MiB body limit of this app's own session manager to BodyLimit's, and the app is not built or does not start if it is not where expected (decision-40, superseding decision-39). BodyLimit's 413 is a JSON-RPC error a client shows.

Verified on real uvicorn with FastMCP's client: 10 MiB and 20 MiB limits, with and without a token, two starts, one byte over, far over, 401 without a token at exactly the limit, and the refusal to start. The hand mutations listed in the reviews are killed, except two without practical effect (see the notes). CI on 082fd86 was green; only wording changed after it.

Open: DoD 2 (test_the_menu_driver_reports_a_failing_cli fails locally on macOS), DoD 4 until the merge. Not verified: a large upload to a real Paperless, clients other than FastMCP's, memory on Linux. Risk: one 100 MiB upload takes 640 to about 890 MB, and nothing limits how many run at once.
<!-- SECTION:FINAL_SUMMARY:END -->
