---
id: PLMCP-112
title: Return webhook URLs without their credentials
status: To Do
assignee: []
created_date: '2026-11-24T11:11:57Z'
labels:
  - safety
dependencies: []
priority: medium
type: bug
ordinal: 91000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
get_workflow, list_workflows, the results of create_workflow and update_workflow and raw_api_get of /api/workflows/ return webhook URLs with user name and password. Found by PLMCP-107 and its review, outside its criteria. A read workflow written back must not lose them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every path that returns a webhook URL leaves out user name and password and says so; writing the shown form back keeps the stored ones
- [ ] #2 Adapter, service, tool and contract tests; decision changes are superseding decisions
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
