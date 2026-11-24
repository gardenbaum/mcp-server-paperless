---
id: PLMCP-80.4
title: 'Bulk changes d: run_workflow'
status: To Do
assignee: []
created_date: '2026-11-24T12:54:35Z'
labels:
  - bulk
dependencies:
  - PLMCP-80.2
parent_task_id: PLMCP-80
priority: medium
type: feature
ordinal: 95000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth part of PLMCP-80: Paperless 3.5.1 can run a workflow with a manual trigger on chosen documents, this server cannot. Design approved by the owner on 2026-11-24 (decisions 1 to 20 as recommended); file design-plmcp-80.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 run_workflow runs a workflow with a manual trigger on named documents after a confirmation that says what the workflow does
- [ ] #2 Contract, tool and E2E tests cover a restricted user, an unusable workflow and a partial failure
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
