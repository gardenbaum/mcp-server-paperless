---
id: PLMCP-95
title: Let HTTP uploads use the configured size limit
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T14:05:31Z'
updated_date: '2026-11-23T14:22:28Z'
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
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
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
No supported way found: FastMCP 4.2.1, 4.2.5 (latest) and its main branch pass no max_request_body_size to the SDK's session manager (mcp 2.3.0 takes it), and there is no setting for it. Prototype on real uvicorn: setting max_body_size of the SDK's RequestBodyLimitMiddleware instance when the app starts lets 10 MiB and 100 MiB files through. Waiting for the owner's decision between the options; docs, decision and tool descriptions follow it.
Done, independent of the option: BodyLimit's 413 is a JSON-RPC error naming the limit in bytes and the setting; before, the client showed only 'Server returned an error response'. Between 4 MiB and this server's limit the SDK's plain 413 still answers.

Owner approved option 1 and the unchanged 100 MiB maximum (2026-11-23). The tool descriptions needed no change: 10 MiB by default is now true over HTTP too. The documented 'three times the file' was wrong and is replaced by measurements. FastMCP issue 854 already asks for the parameter.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Over HTTP, files up to PAPERLESS_MCP_MAX_UPLOAD_BYTES reach the upload tools: app.raise_sdk_limit sets the MCP SDK's 4 MiB body limit of this app's own session manager to BodyLimit's, and the app refuses to start if it is not where expected (decision-40, superseding decision-39). BodyLimit's 413 is a JSON-RPC error a client shows.

Verified on real uvicorn with FastMCP's client: 10 MiB and 20 MiB limits, with and without a token, one byte over, far over, and 401 without a token; hand mutations killed. just check passes except test_the_menu_driver_reports_a_failing_cli (known on macOS).

Open: DoD 2 (CI and that macOS failure), 3 and 4 wait for the pull request. Not verified: e2e against Paperless, Docker, Python 3.15, clients other than FastMCP's. Risk: one 100 MiB upload takes 640 to 850 MB.
<!-- SECTION:FINAL_SUMMARY:END -->
