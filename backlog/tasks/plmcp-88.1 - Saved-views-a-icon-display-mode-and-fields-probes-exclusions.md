---
id: PLMCP-88.1
title: 'Saved views a: icon, display mode and fields, probes, exclusions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T10:18:26Z'
updated_date: '2026-11-25T11:00:00Z'
labels:
  - views
dependencies: []
parent_task_id: PLMCP-88
priority: medium
type: feature
ordinal: 104000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-88: icon, display_mode and display_fields of a saved view can neither be read nor set, and how Paperless 3.5.1 treats them, grants and the UI settings decides the next parts (probes P1 to P5). Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen'); file design-plmcp-88.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Probes P1 to P5 ran against Paperless 3.5.1 on GitHub and their results are recorded
- [x] #2 icon, display_mode and display_fields can be read, set on creation and changed, and the last two cleared; wrong values are refused before anything is sent
- [x] #3 The owner-approved exclusions (PUT, ordering and full_perms of the list, owner and set_permissions on POST) are recorded with quote and date
- [x] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each field and clearing
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
1. Probes P1 to P5 as temporary live tests through a draft PR; results in notes and decision.
2. Tests first, bottom up: domain, adapter, contract on fake and live, service, tool, E2E for icon, display_mode, display_fields.
3. Coverage table with exclusions, decision, docs, snapshots.
4. Full hosted E2E only on the last head of the stack.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner's words (2026-11-24, question dialog 'Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?'): 'Alle wie empfohlen' (all as recommended).

Probes: CI run 45594222648, job 139135939753, commit c549208 (tests/e2e/test_view_probes.py, deleted afterwards). P1 to P4 as designed, P5 read in the source of v3.5.1; details in decision-91. New: Paperless takes a deleted custom field out of views (fake and contract test follow).
Red first: domain, service and contract modules failed to import, adapter 8, tool 2 tests red, then green; the contract test for deleted custom fields red on the fake. E2E written before its first hosted run. 26 hand mutants, 1 survived (a duplicate check, deleted).
Deliberate: surface and message snapshots; hint of "Nothing to change". Guide limit unchanged.
just check was not run as a whole locally.

CI run 45596307049 on 65cd16b: live contract (test_views 25) and E2E (test_views 5) green on Paperless 3.5.1.
<!-- SECTION:NOTES:END -->
