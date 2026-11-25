---
id: PLMCP-88.3
title: 'Saved views c: owner and grants of saved views'
status: To Do
assignee: []
created_date: '2026-11-25T10:18:28Z'
labels:
  - views
dependencies:
  - PLMCP-88.2
parent_task_id: PLMCP-88
priority: medium
type: feature
ordinal: 106000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-88: owner and grants of a saved view cannot be read or set. Shares the single read with PLMCP-81.5. Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen'); file design-plmcp-88.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 set_permissions takes saved views by name or id with the confirmation of decisions 12 and 19, read one by one
- [ ] #2 get_saved_view shows owner and grants with names
- [ ] #3 The remaining rows of saved_views in docs/api-coverage.toml are covered
- [ ] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover owner, non-owner, superuser and unowned views, with and without confirm
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
