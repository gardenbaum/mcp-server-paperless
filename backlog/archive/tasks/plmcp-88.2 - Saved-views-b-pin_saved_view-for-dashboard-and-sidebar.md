---
id: PLMCP-88.2
title: 'Saved views b: pin_saved_view for dashboard and sidebar'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-25T10:18:27Z'
updated_date: '2026-11-25T14:46:42Z'
labels:
  - views
dependencies:
  - PLMCP-88.1
parent_task_id: PLMCP-88
priority: medium
type: feature
ordinal: 105000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-88: in API 10 the dashboard and the sidebar of a user live in /api/ui_settings/, whose POST replaces everything. Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen'); file design-plmcp-88.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Whether a view is on the dashboard and in the sidebar of the current user can be read and written, also for views one may only see, and the tool says it applies to that user only
- [x] #2 Every other UI setting survives a write, the settings are compared afterwards, a difference names keys and never values, and nothing is repeated
- [x] #3 POST /api/ui_settings/ is covered for its saved-view part; the rest stays open under PLMCP-83
- [x] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E (stdio and HTTP) cover pinning, unpinning, missing permissions and surviving settings
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
1. Tests first: adapter (GET bounded, computed keys stripped, POST body), contract on fake and live, service (matrix, other writer, lost answer, malformed, parallel calls, property), tool, E2E stdio and HTTP.
2. UiSettingsReader/Writer, paperless/ui_settings.py, services/view_pins.py, pin_saved_view, on_dashboard/in_sidebar.
3. decision-92, coverage, docs, snapshots, guide limit +17 (approved).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented, decision-92. Red first: adapter and service modules failed to import, contract 4 and tool 4 tests red on the fake, then green; E2E (memory, stdio, HTTP) written before its first hosted run. 35 hand mutants, 10 survived first (9 closed by tests, one by deleting code), then none.
Beyond the design: calls of one server wait for each other (parallel tool calls would undo each other); an answer to create or update also shows where the view is (one request more).
For PLMCP-83: POST /api/ui_settings/ counts as covered in the table; the other settings stay open there (Limits text of api-coverage.md).
just check was not run as a whole locally.

CI run 45598528336 on 7061254: e2e parts green on Paperless 3.5.1 (contract of ui_settings and views, E2E of view_pins over memory, stdio and HTTP).

Review: a refused second read, the texts and foreign answers (redirect, login page) tested and named truly; what a late POST would do is said (NOT_THERE, decision-92); a call waits at most 40 s for another; configuration.md wrapped. Tests first (7 red); 44 mutants, none survives.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
pin_saved_view puts a view on the dashboard or in the sidebar of the current user and takes it off; every other UI setting is sent back and compared, nothing is repeated (decision-92). Merged as 769fea7 (#165). Live over memory, stdio and HTTP: 45598528336 (7061254); CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. DoD 2 stays open: just check was not run as a whole locally. On GitHub the check jobs run its recipes one by one: 45616084339 (6193c23).
<!-- SECTION:FINAL_SUMMARY:END -->
