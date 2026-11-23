---
id: PLMCP-82
title: Tell the assistant which toolsets are off and how to get them
status: To Do
assignee: []
created_date: '2026-11-23T12:14:52Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 70000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An assistant saw only the default profile and reported trash, PDF tools, bulk changes, mail, workflows, views and tasks as missing, although the server has them. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_instance_overview and the server instructions name the toolsets that are off, what each does, and the setting and restart that turn them on; nothing is shown that read-only mode, disabled tools or a missing capability excludes
- [ ] #2 It is right for stdio, HTTP, the plugin and the container image
- [ ] #3 Whether toolsets can be switched on per session is examined in a decision (security: the operator keeps the last word)
- [ ] #4 The cost in tokens is measured and stays within the budget
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
