---
id: PLMCP-79.3
title: 'PDF tools c: metadata per part, safe replacing, extract_pages'
status: To Do
assignee: []
created_date: '2026-11-24T08:51:16Z'
labels: []
dependencies:
  - PLMCP-79.1
parent_task_id: PLMCP-79
priority: medium
type: feature
ordinal: 88000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-79 (design approved by the owner on 2026-11-24 (all 14 decisions as recommended); file design-plmcp-79.md of the main agent, sections 2.3, 2.4).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Split sets title, type, correspondent, date and tags per part; every part reports state, metadata and the next call
- [ ] #2 extract_pages keeps or removes the pages of the source; every failure row of the design is tested with its state
- [ ] #3 Nothing is trashed while a part is unfinished, failed, hidden, duplicate, stopped or without its metadata
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
