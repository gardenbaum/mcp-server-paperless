---
id: PLMCP-87.3
title: 'Workflows c: manual triggers and AI suggestions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:43:06Z'
updated_date: '2026-11-25T09:19:48Z'
labels:
  - workflows
dependencies:
  - PLMCP-87.2
parent_task_id: PLMCP-87
priority: medium
type: feature
ordinal: 101000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-87: run_workflow runs manual triggers, but only the web UI can make them; applying AI suggestions is only made there too. Design approved by the owner on 2026-11-24, all recommendations (file design-plmcp-87.md of the main agent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Manual triggers and the action applying AI suggestions can be created and changed, the latter confirmed while enabled
- [x] #2 Password removal stays with the web UI and is recorded as an owner-approved exclusion
- [x] #3 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover both types
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Tests first: domain types, service (create, update, part), contract on fake and 3.5.1, tools, E2E.
2. Manual triggers: filters Paperless reads (decision-75); AI suggestions: the three settings, confirmed with the wording of decision-75.
3. Exclusion of password removal, decision-86 (supersedes that part of decision-21), docs, snapshots.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24): 'Alle wie empfohlen' (question quoted in decision-81), here 4a and 5a. Implemented on plmcp-87-3-types (PR #160, stacked on #159), decision-86 (created by the CLI in a copy of backlog/, 83 is taken elsewhere). Red first at domain, service, contract (fake); tool and E2E tests written with them. 12 hand mutants, all killed. Full hosted E2E 45576104128 on f7bbcbe dispatched; AC 3 waits for it.

Full hosted E2E 45578885550 on f238efc green (contract 35, e2e 10, parts 6 of the workflow modules). After it only README numbers, the backlog archive of main and this note changed.
<!-- SECTION:NOTES:END -->
