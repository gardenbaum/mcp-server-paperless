---
id: PLMCP-88.2
title: 'Saved views b: pin_saved_view for dashboard and sidebar'
status: To Do
assignee: []
created_date: '2026-11-25T10:18:27Z'
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
- [ ] #1 Whether a view is on the dashboard and in the sidebar of the current user can be read and written, also for views one may only see, and the tool says it applies to that user only
- [ ] #2 Every other UI setting survives a write, the settings are compared afterwards, a difference names keys and never values, and nothing is repeated
- [ ] #3 POST /api/ui_settings/ is covered for its saved-view part; the rest stays open under PLMCP-83
- [ ] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E (stdio and HTTP) cover pinning, unpinning, missing permissions and surviving settings
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
