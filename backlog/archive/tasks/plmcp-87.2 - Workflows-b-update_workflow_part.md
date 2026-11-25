---
id: PLMCP-87.2
title: 'Workflows b: update_workflow_part'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T22:43:05Z'
updated_date: '2026-11-25T14:46:41Z'
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
- [x] #1 One trigger or action can be added, changed or removed while the others keep ids, settings and hidden values
- [x] #2 Every operation keeps decision-21 and decision-66; removing a part with hidden values is confirmed, also while the workflow is disabled, in update_workflow too
- [x] #3 The answer is compared with the plan and a difference or a lost answer is reported, never repeated
- [x] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the writes, refusals and hidden values
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Tests first, bottom up: port and adapter (one PATCH of one list, parts by id), contract on fake and 3.5.1, services/workflow_parts.py, tool update_workflow_part, E2E.
2. The comparison after a write uses a fresh read (P1).
3. Hidden values lost: confirmed in the new tool and in update_workflow, also while disabled.
4. decision-82, coverage, docs, snapshots, guide limit 2943 to 2966 (approved, 23 bytes of 42).
5. Draft PR stacked on 87.1, one full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented on plmcp-87-2-update-part (PR #159, stacked on #154), decision-82. Red first at adapter, contract, service and tool level; E2E written before its first hosted run. workflow_parts.py 100 % (branches). 42 hand mutants: 7 survived first and were killed by new tests (41 ran; one pattern was absent).
Full hosted E2E 45569964951 on bb58f15 green: tests/contract/test_workflows.py 31, tests/e2e/test_workflows.py 9, tests/e2e/test_workflow_parts.py 6 passed.
Deviation: trigger_id/action_id instead of part_id (numbered apart in Paperless). Beyond the design: no rewrite of actions while a kept one is held by another workflow with several actions. decision-82 was created by the CLI in a copy of backlog/ outside the repository, because the CLI here skips to 84 (83 is on the branch of 81.3).
just check was not run as a whole locally.

Owner, 2026-11-25: 'Überall wie von dir empfohlen bitte.' (everywhere as you recommend, please): asking also on disabled workflows, the order rule for shared actions and an invalid stored trigger blocking the others stay as built (decision-82).

Independent review of #159 (efaadb0..bb58f15): H1, M1 to M5, L1, L3 to L6 and the survivors fixed, tests first (28 red). 26 further hand mutants, 4 survived and were closed (two by deleting code, two by tests). For the owner: M4 values of custom fields are merged, the result says how many stay; M5 the workflow is read again right before the PATCH and a difference refuses the call (one request more); the milliseconds after it remain.

Second review of #159 (N1 to N3, L2, L6 to L8, survivors) fixed, tests first. N1: values of fields no longer assigned are dropped from what is written.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
update_workflow_part adds, changes, moves or removes one trigger or action; the others keep ids, settings and hidden values, every write is compared with a fresh read and never repeated (decision-82). Merged as 15b5f4c (#159). Full E2E 45578660467 (ddd2d72); CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. DoD 2 stays open: just check was not run as a whole locally. On GitHub the check jobs run its recipes one by one: 45586010679 (055e4a8).
<!-- SECTION:FINAL_SUMMARY:END -->
