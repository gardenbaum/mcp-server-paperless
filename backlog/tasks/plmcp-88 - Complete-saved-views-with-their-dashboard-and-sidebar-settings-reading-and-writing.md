---
id: PLMCP-88
title: >-
  Complete saved views with their dashboard and sidebar settings, reading and
  writing
status: In Progress
assignee: []
created_date: '2026-11-23T12:22:02Z'
updated_date: '2026-11-25T14:53:17Z'
labels: []
dependencies:
  - PLMCP-91
priority: medium
type: feature
ordinal: 70200
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset views (off by default) reads and writes name, rules, sort and page size; the owner's agent did not see it. SavedView also stores icon, display_mode, display_fields, owner and permissions. Dashboard and sidebar visibility left the saved view in API 10 and live in the user's UI settings (/api/ui_settings/). Split from PLMCP-81. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every field of SavedView (icon, display_mode, display_fields, owner, permissions) can be read and written
- [x] #2 Dashboard and sidebar visibility of a view can be read and written in /api/ui_settings/ of the current user, without losing other settings; the tool says it applies to that user only
- [x] #3 Owner and permission changes follow decisions 12 and 19 (confirm)
- [x] #4 Every endpoint of saved_views and the saved-view part of ui_settings in the vendored 3.5.1 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-91)
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover every field, clearing, sharing with and without confirm, and that other UI settings survive a write
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-91 (coverage table): POST /api/ui_settings/ is listed open under this task for the saved-view part (dashboard and sidebar visibility of a view, AC 2). The rest of /api/ui_settings/ beyond saved views belongs to PLMCP-83; one table entry cannot name both. Also: a covered GET /api/ui_settings/ does not mean the view visibility in its answer is shown.

Reopened 2026-11-25: a criterion that names several test levels is read literally, every item at every named level. All parts merged (#164, #165, #166), 88.1 archived. AC 1 to 4 are proven. AC 5, missing: contract tests of sharing with and without confirm; see also 88.2 (AC 4) and 88.3 (AC 4). CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. DoD 1 stays open: E2E tests of the parts were not seen red; DoD 2: see the parts; DoD 4: merged through the parts' pull requests, no own one.
<!-- SECTION:NOTES:END -->
