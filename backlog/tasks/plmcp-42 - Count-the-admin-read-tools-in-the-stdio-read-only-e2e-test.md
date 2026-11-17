---
id: PLMCP-42
title: Count the admin read tools in the stdio read-only e2e test
status: Done
assignee: []
created_date: '2026-11-17T06:24:00Z'
updated_date: '2026-11-17T06:24:00Z'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 42000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The post-merge e2e run on main (CI run 44020154904) failed in tests/e2e/test_stdio.py::test_the_environment_selects_the_tools_over_stdio: the admin toolset (PLMCP-33) added the read-only tools get_user, get_group, get_app_config, list_tasks, list_logs, get_log and bulk_download, and the expected read-only set was not updated. E2E no longer runs on pull requests (decision-24), so this surfaced only after the merge.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The expected read-only set lists the admin read tools
- [x] #2 tests/e2e/test_stdio.py passes against the live stack
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the seven admin read tools to the expected read-only set in the stdio e2e test. Red on main CI run 44020154904, green locally against the live stack (3 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
