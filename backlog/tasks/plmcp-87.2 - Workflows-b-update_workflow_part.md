---
id: PLMCP-87.2
title: 'Workflows b: update_workflow_part'
status: To Do
assignee: []
created_date: '2026-11-24T22:43:05Z'
labels:
  - workflows
dependencies:
  - PLMCP-87.1
parent_task_id: PLMCP-87
priority: medium
type: feature
ordinal: 100000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-87: one trigger or action cannot be changed without resending the others, which loses secrets and refuses workflows with types this server cannot write. Design approved by the owner on 2026-11-24, all recommendations (file design-plmcp-87.md of the main agent). Waits for the result of probe P1.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One trigger or action can be added, changed or removed while the others keep ids, settings and hidden values
- [ ] #2 Every operation keeps decision-21 and decision-66; removing a part with hidden values is confirmed, also while the workflow is disabled, in update_workflow too
- [ ] #3 The answer is compared with the plan and a difference or a lost answer is reported, never repeated
- [ ] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the writes, refusals and hidden values
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
