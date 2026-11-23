---
id: PLMCP-65
title: Treat an empty PAPERLESS_PUBLIC_URL as unset
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T09:17:46Z'
updated_date: '2026-11-23T09:58:05Z'
labels: []
dependencies: []
priority: low
type: bug
ordinal: 59000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
MCP clients that build a form from server.json (PLMCP-61) may pass optional variables the user left blank as empty strings. An empty PAPERLESS_PUBLIC_URL fails URL validation, so the server refuses to start although the variable is optional. Found in the independent review of PR 82.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An empty PAPERLESS_PUBLIC_URL starts the server and links use PAPERLESS_URL
- [x] #2 An empty PAPERLESS_URL or PAPERLESS_TOKEN is still refused at startup
- [x] #3 Tests cover whether an empty value of each other optional stdio variable starts the server, and the outcome is decided deliberately
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
1. Failing tests for empty values. 2. env_ignore_empty on PaperlessSettings only, as OidcSettings does. 3. Docs, gates, opus review, Codex.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
env_ignore_empty on PaperlessSettings only (as OidcSettings). Decided: empty READ_ONLY and TOOLSETS stay refused, because unset would offer writes or more tools than meant; empty numbers and LOG_LEVEL stay refused with a clear error (server.json lists their defaults); empty DISABLED_TOOLS and UPLOAD_DIRS are empty lists.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
An empty PAPERLESS_PUBLIC_URL counts as unset, so clients that pass blank optional fields can start the server; empty PAPERLESS_URL and PAPERLESS_TOKEN are still refused. Empty values of every other stdio variable are pinned by tests. DoD 2: CI is the evidence (one known macOS-only local failure); DoD 4 true after merge.
<!-- SECTION:FINAL_SUMMARY:END -->
