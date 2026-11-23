---
id: PLMCP-82
title: Tell the assistant which toolsets are off and how to get them
status: To Do
assignee: []
created_date: '2026-11-23T12:14:52Z'
updated_date: '2026-11-23T12:21:58Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 62000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An assistant saw only the default profile and reported trash, PDF tools, bulk changes, mail, workflows, views and tasks as missing, although the server has them. Neither the server instructions nor get_instance_overview says anything about toolsets. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_instance_overview and the server instructions name the toolsets that are off, what each does, and the setting and restart that turn them on; nothing is shown that read-only mode, disabled tools or a missing capability excludes
- [ ] #2 It is right for stdio, HTTP, the plugin and the container image
- [ ] #3 Whether toolsets can be switched on per session is examined in a decision (security: the operator keeps the last word)
- [ ] #4 The added tokens (instructions and overview) are measured and stay under a limit that this task sets and a test enforces (today's budget counts tools only)
- [ ] #5 Contract tests on the fake and Paperless 3.4.2, tool tests and E2E cover every combination of selected toolsets, read-only mode, disabled tools and a missing capability
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
