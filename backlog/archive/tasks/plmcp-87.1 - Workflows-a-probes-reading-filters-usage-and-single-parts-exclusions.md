---
id: PLMCP-87.1
title: 'Workflows a: probes, reading filters, usage and single parts, exclusions'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T22:43:04Z'
updated_date: '2026-11-25T14:46:41Z'
labels:
  - workflows
dependencies:
  - PLMCP-80.4
parent_task_id: PLMCP-87
priority: medium
type: feature
ordinal: 99000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-87. How Paperless 3.5.1 treats parts sent with ids decides how single triggers and actions can be written (probe P1), and today nobody can find the workflows that use an object or read one trigger or action. Design approved by the owner on 2026-11-24, all recommendations (file design-plmcp-87.md of the main agent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Probes P1 to P5 ran against Paperless 3.5.1 on GitHub and their results are recorded; P1 decides the mechanism of the writing part
- [x] #2 list_workflows filters by enabled, by trigger type and by a used tag, correspondent, document type, storage path, custom field, user, group or mail rule, and names the part and setting that use it
- [x] #3 get_workflow reads one trigger or action and the workflows that hold it, also without the permission to view parts where the workflows may be viewed
- [x] #4 Hidden values (webhook headers, URL credentials, passwords) are not shown on any new path
- [x] #5 The owner-approved exclusions 2 and 3 are recorded with quote and date, and the covered rows are covered
- [x] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each new read
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
1. Probes P1 to P5 as live tests through a draft PR; keep what pins behaviour the next part rests on.
2. Tests first, bottom up: adapter (list filters, single parts), contract on fake and live, service (usage search, parts), tool, E2E.
3. Coverage table with exclusions 2 and 3, decision, docs, snapshots.
4. One full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24, question dialog, recommended options chosen; record of the main agent): 'Designs 81, 87, 88, 89, 90, 83, 100 all as recommended'. Guide limit: this part needs none of the 42 bytes (no new tool).

Probes on Paperless 3.5.1, CI run 45503786740, job e2e (1/6), head 54845e1 (temporary tests, removed): P1 holds: id-only and full PATCHes kept all five trigger and eight action types with hidden values, one changed part changed nothing else; the one red case was the test's own expectation: the answer of a PATCH that moves actions does not list them in their order, so part b has to compare with a fresh read. P2 to P5 confirm T1 to T7 and T13 of the design (details in decision-81).

Owner's words (2026-11-24, question dialog 'Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?'): 'Alle wie empfohlen'.

Full hosted E2E 45507183680 on 6d12e49 green: tests/e2e/test_workflow_parts.py 6 passed (P1 with move, add, remove against a fresh read), tests/contract/test_workflows.py 25 and tests/e2e/test_workflows.py 6 passed. Then only main was merged and the quote set.

Probe log: https://github.com/gardenbaum/mcp-server-paperless/actions/runs/45503786740/job/138833596559 (raw log of the job): P2 from line 2469 ('patch email': 500, 'post webhook': 500, 'patch consumption sources': 400, 'delete held': 204, rights per permission), P3 from line 2562 (orphans true until 'after another update'), P4 from line 2592 ('vanished trigger id-only' sources 1 to 4 without a filter, 'victim after': thief, 'post trigger id' 47/47, 'post action id' 63/62, rules 400/400/200), P5 from line 2799 ('ai enabled' 201, 'ai consumption' 400, list counts).

Review of #154: M1 (digits as a name, note for every kind), L1 (the live P1 test proves what was sent is stored), L2 (WorkflowRead.note removed), L3 (ids from 1, too many workflows leave the holders unknown) fixed, tests first. Surface snapshot: get_workflow only.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
list_workflows filters by enabled, trigger type and a used object and names the place; get_workflow reads one trigger or action and the workflows that hold it, hidden values stay hidden (decision-81). Probes P1 to P5: run 45503786740. Merged as 738ee5a (#154). Full E2E 45507183680 (6d12e49); CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. DoD 2 stays open: just check was not run as a whole locally. On GitHub the check jobs run its recipes one by one: 45568792562 (efaadb0).
<!-- SECTION:FINAL_SUMMARY:END -->
