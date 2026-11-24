---
id: PLMCP-87.1
title: 'Workflows a: probes, reading filters, usage and single parts, exclusions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:43:04Z'
updated_date: '2026-11-24T23:00:17Z'
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
- [ ] #1 Probes P1 to P5 ran against Paperless 3.5.1 on GitHub and their results are recorded; P1 decides the mechanism of the writing part
- [ ] #2 list_workflows filters by enabled, by trigger type and by a used tag, correspondent, document type, storage path, custom field, user, group or mail rule, and names the part and setting that use it
- [ ] #3 get_workflow reads one trigger or action and the workflows that hold it, also without the permission to view parts where the workflows may be viewed
- [ ] #4 Hidden values (webhook headers, URL credentials, passwords) are not shown on any new path
- [ ] #5 The owner-approved exclusions 2 and 3 are recorded with quote and date, and the covered rows are covered
- [ ] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each new read
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
1. Probes P1 to P5 as live tests through a draft PR; keep what pins behaviour the next part rests on.
2. Tests first, bottom up: adapter (list filters, single parts), contract on fake and live, service (usage search, parts), tool, E2E.
3. Coverage table with exclusions 2 and 3, decision, docs, snapshots.
4. One full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24, question dialog, recommended options chosen; record of the main agent): 'Designs 81, 87, 88, 89, 90, 83, 100 all as recommended'. Guide limit: this part needs none of the 42 bytes (no new tool).

Probes on Paperless 3.5.1, CI run 45503786740, job e2e (1/6), head 54845e1 (temporary tests, removed): P1 holds: id-only and full PATCHes kept all five trigger and eight action types with hidden values, one changed part changed nothing else; the one red case was the test's own expectation: the answer of a PATCH that moves actions does not list them in their order, so part b has to compare with a fresh read. P2 to P5 confirm T1 to T7 and T13 of the design (details in decision-81).
<!-- SECTION:NOTES:END -->
