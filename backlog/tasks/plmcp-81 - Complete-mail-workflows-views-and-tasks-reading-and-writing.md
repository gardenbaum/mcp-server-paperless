---
id: PLMCP-81
title: Complete mail, workflows, views and tasks, reading and writing
status: To Do
assignee: []
created_date: '2026-11-23T12:14:51Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 69000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolsets mail, workflows, views and admin (off by default) already read and write most of it. The owner's agent did not see them. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Mail: read one processed mail, delete processed-mail records so a mail is fetched again, test an unsaved account, and dry-run or explain why a rule did not match; OAuth accounts as far as the API allows
- [ ] #2 Workflows: switch on and off, reorder, read and change single triggers and actions, find the workflows that use an object, and explain why one did not run, as far as the API allows
- [ ] #3 Views: everything a saved view stores can be read and written, including dashboard, sidebar and display settings and sharing
- [ ] #4 Tasks: active tasks, summaries and status counts are readable, filters are complete, tasks can be started and acknowledged singly and in bulk
- [ ] #5 Document history and the statistics needed for troubleshooting are readable
- [ ] #6 A read-only use of these toolsets needs no write tool to be offered (per toolset, not only for the whole server)
- [ ] #7 Every endpoint of these areas is covered or its omission recorded with a reason; live tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
