---
id: PLMCP-120
title: Close the open test criteria of PLMCP-89 and 90.1
status: To Do
assignee: []
created_date: '2026-11-25T16:27:17Z'
labels:
  - testing
dependencies: []
priority: medium
type: task
ordinal: 111000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The code of PLMCP-89 (89.1 to 89.3) and 90.1 is merged, but these tasks stay open because a criterion that names several test levels is read literally: every item has to be covered at every named level (main agent, 2026-11-25; a more lenient reading would be the owner's decision), as in PLMCP-119. Each criterion here names the task, its criterion and the level of missing tests; the task itself is ticked when its tests are on main. The criteria of the tasks are not changed. Not here: the release notes of PLMCP-89.1 AC 1 belong to the task of the 0.5.0 release (not created yet on 2026-11-25; a draft of the text is in the notes of 89.1), and AC 3 of PLMCP-90.1 is PLMCP-90.3. A test that cannot be made at its level is put to the owner, never left out silently.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 PLMCP-89.1 AC 5, contract (fake and 3.5.1): the moved toolset (the task tools are in tasks and not in admin)
- [ ] #2 PLMCP-89.1 AC 5, tool: list_tasks with each ordering not asserted yet: created and finished ascending, -created given, state descending, task_type, duration_seconds and wait_time_seconds both ways, and the effect of -finished
- [ ] #3 PLMCP-89.1 AC 5, tool: the effect of the filters acknowledged and result of list_tasks (only sent in test_arguments now)
- [ ] #4 PLMCP-89.1 AC 5, tool: the types run_task refuses
- [ ] #5 PLMCP-89.1 AC 5, E2E: list_tasks with every ordering other than -finished, and the order itself
- [ ] #6 PLMCP-89.1 AC 5, E2E: the types run_task refuses
- [ ] #7 PLMCP-89.2 AC 5, contract (fake and 3.5.1): starting train_classifier and llm_index
- [ ] #8 PLMCP-89.2 AC 5, tool: what the summary by type of get_task_overview holds
- [ ] #9 PLMCP-89.2 AC 5, tool: acknowledging all as a user who is not staff
- [ ] #10 PLMCP-89.2 AC 5, tool: starting train_classifier and llm_index
- [ ] #11 PLMCP-89.2 AC 5, E2E: acknowledging all as a user who is not staff
- [ ] #12 PLMCP-89.3 AC 4, contract (fake and 3.5.1): paging 60 changes of the history
- [ ] #13 PLMCP-89 AC 8, contract, tool and E2E: what the criteria for 89.1 (each filter), 89.2 and 89.3 above name
- [ ] #14 PLMCP-90.1 AC 5, contract (fake and 3.5.1): downloads of a read-only toolset, prompts that need a hidden tool and the error messages of a wrong selection
- [ ] #15 PLMCP-90.1 AC 5, tool over stdio and over HTTP on both protocol eras: the error messages of a wrong selection
- [ ] #16 PLMCP-90.1 AC 5, E2E: the downloads of a read-only toolset
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
