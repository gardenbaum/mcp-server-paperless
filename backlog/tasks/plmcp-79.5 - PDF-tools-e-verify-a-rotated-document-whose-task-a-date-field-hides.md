---
id: PLMCP-79.5
title: 'PDF tools e: verify a rotated document whose task a date field hides'
status: To Do
assignee: []
created_date: '2026-11-24T13:42:49Z'
labels:
  - pdf
dependencies:
  - PLMCP-79.3
  - PLMCP-79.4
parent_task_id: PLMCP-79
priority: low
type: enhancement
ordinal: 96000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A date custom field hides the consume_file task of a rotated document, so rotate_documents can only answer unknown. The newest version before and after tells whether the rotation is done. Owner approval of 2026-11-24: own task after PLMCP-79.4.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 rotate_documents reports a finished rotation also when a date field hides its task, by comparing the newest version before and after
- [ ] #2 A version made by somebody else meanwhile is never taken for the rotation
- [ ] #3 Contract, service and tool tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
