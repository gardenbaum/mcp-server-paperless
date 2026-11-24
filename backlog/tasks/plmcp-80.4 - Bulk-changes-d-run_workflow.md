---
id: PLMCP-80.4
title: 'Bulk changes d: run_workflow'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:35Z'
updated_date: '2026-11-24T18:23:40Z'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read the 3.5.1 source of run_workflow (bulk_edit, tasks, actions, matching).
2. Tests first, red then green, bottom up: adapter (WorkflowWriter.run_workflow, TaskWaiter row), contract on fake and live, service (services/workflow_runs.py), tool (run_workflow in workflows, opt-in), E2E.
3. Selection arguments move from tools/bulk.py to tools/_shared.py; services/selection.py and the action texts of decision-21 are reused.
4. Registry, budgets (guide limit 2928 to 2943, owner-approved), coverage table, docs, snapshots, decision-75.
5. Draft PR, one full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24): 'Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits.' Here: 11a (tool run_workflow in the toolset workflows), 14 (guide limit to at most 2943).
<!-- SECTION:NOTES:END -->
