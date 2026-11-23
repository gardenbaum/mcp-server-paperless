---
id: PLMCP-90
title: Select a toolset read-only
status: To Do
assignee: []
created_date: '2026-11-23T12:22:04Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 70400
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Read-only mode applies to the whole server, so reading mail rules, workflows, views or tasks means either offering their write tools too or giving up every write. Split from PLMCP-81. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The operator can select a toolset read-only (per toolset); no write tool of it is then offered
- [ ] #2 Server-wide read-only mode still wins, and a misspelt selection stops the server as today
- [ ] #3 stdio, HTTP, the plugin and the container are covered, and what PLMCP-82 tells the assistant is right
- [ ] #4 A decision records the setting and how it extends decisions 4 and 6
- [ ] #5 Contract tests on the fake and Paperless 3.4.2, tool tests and E2E cover mixed selections, prompts that need a hidden tool (decision-20) and the error messages
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
