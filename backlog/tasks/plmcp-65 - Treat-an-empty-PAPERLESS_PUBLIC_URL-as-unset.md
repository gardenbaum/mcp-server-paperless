---
id: PLMCP-65
title: Treat an empty PAPERLESS_PUBLIC_URL as unset
status: To Do
assignee: []
created_date: '2026-11-23T09:17:46Z'
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
- [ ] #1 An empty PAPERLESS_PUBLIC_URL starts the server and links use PAPERLESS_URL
- [ ] #2 An empty PAPERLESS_URL or PAPERLESS_TOKEN is still refused at startup
- [ ] #3 Tests cover whether an empty value of each other optional stdio variable starts the server, and the outcome is decided deliberately
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
