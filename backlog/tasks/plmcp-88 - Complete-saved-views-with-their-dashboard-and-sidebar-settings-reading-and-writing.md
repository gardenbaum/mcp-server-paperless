---
id: PLMCP-88
title: >-
  Complete saved views with their dashboard and sidebar settings, reading and
  writing
status: To Do
assignee: []
created_date: '2026-11-23T12:22:02Z'
labels: []
dependencies: []
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
- [ ] #1 Every field of SavedView (icon, display_mode, display_fields, owner, permissions) can be read and written
- [ ] #2 Dashboard and sidebar visibility of a view can be read and written in /api/ui_settings/ of the current user, without losing other settings; the tool says it applies to that user only
- [ ] #3 Owner and permission changes follow decisions 12 and 19 (confirm)
- [ ] #4 Every endpoint of saved_views and the saved-view part of ui_settings in the 3.4 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-83 once it exists)
- [ ] #5 Contract tests on the fake and Paperless 3.4.2, tool tests and E2E cover every field, clearing, sharing with and without confirm, and that other UI settings survive a write
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
