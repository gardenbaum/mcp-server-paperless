---
id: PLMCP-83.4
title: 'Rest of the API d: get_permissions, second factor, update check'
status: To Do
assignee: []
created_date: '2026-11-25T14:42:19Z'
labels: []
dependencies:
  - PLMCP-83.3
  - PLMCP-89
parent_task_id: PLMCP-83
type: feature
ordinal: 110000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Last part of PLMCP-83; needs PLMCP-89.3 (get_system_report). Design approved by the owner on 2026-11-24 with all recommendations ('Alle wie empfohlen', all as recommended); file design-plmcp-83.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_permissions shows owner and grants of 1 to 100 objects by name without changing them
- [ ] #2 update_user(remove_second_factor=true) asks first and refuses the server's own account
- [ ] #3 get_system_report(check_for_update=true) asks for the newest release within its own bound and says unknown where it cannot
- [ ] #4 Nothing stands open under PLMCP-83 in docs/api-coverage.toml
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
