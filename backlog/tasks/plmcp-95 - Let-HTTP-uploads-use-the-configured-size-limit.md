---
id: PLMCP-95
title: Let HTTP uploads use the configured size limit
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T14:05:31Z'
updated_date: '2026-11-23T14:12:37Z'
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
- [ ] #1 Over HTTP an upload up to PAPERLESS_MCP_MAX_UPLOAD_BYTES reaches the tool, at the default and at a larger setting, proven on a real server
- [ ] #2 A body over this server's limit still gets 413 and nothing is read before authentication
- [ ] #3 It uses a supported way of FastMCP or the SDK, or a decision says why not and what breaks it; a dependency update that brings the cap back fails a test
- [ ] #4 A client gets an error it can act on when a file is too large
- [ ] #5 docs, decision-39 and the tool descriptions tell the truth for stdio and HTTP
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
1. Look for a supported way to raise the SDK's 4 MiB cap (FastMCP 4.2.1 and 4.2.5: none), prototype the options on real uvicorn, report them for a decision.
2. Independent of that: this server's 413 becomes a JSON-RPC error, which the SDK client shows; test first.
3. After the decision: raise the cap, real-server tests, drift test, decision, docs and tool descriptions.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
No supported way found: FastMCP 4.2.1, 4.2.5 (latest) and its main branch pass no max_request_body_size to the SDK's session manager (mcp 2.3.0 takes it), and there is no setting for it. Prototype on real uvicorn: setting max_body_size of the SDK's RequestBodyLimitMiddleware instance when the app starts lets 10 MiB and 100 MiB files through. Waiting for the owner's decision between the options; docs, decision and tool descriptions follow it.
Done, independent of the option: BodyLimit's 413 is a JSON-RPC error naming the limit in bytes and the setting; before, the client showed only 'Server returned an error response'. Between 4 MiB and this server's limit the SDK's plain 413 still answers.
<!-- SECTION:NOTES:END -->
