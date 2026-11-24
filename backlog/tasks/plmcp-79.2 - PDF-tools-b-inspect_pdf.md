---
id: PLMCP-79.2
title: 'PDF tools b: inspect_pdf'
status: To Do
assignee: []
created_date: '2026-11-24T08:51:15Z'
labels: []
dependencies:
  - PLMCP-79.1
parent_task_id: PLMCP-79
priority: medium
type: feature
ordinal: 87000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-79 (design approved by the owner on 2026-11-24 (all 14 decisions as recommended); file design-plmcp-79.md of the main agent, section 2.1).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 inspect_pdf gives page count, sizes, rotation and contact sheets within the limits of decision-42
- [ ] #2 OVERVIEW_GUIDE_LIMIT rises for the three tools of PLMCP-79 at most to 2880 (owner, 2026-11-24)
- [ ] #3 Rendering, service, tool, contract and E2E tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
