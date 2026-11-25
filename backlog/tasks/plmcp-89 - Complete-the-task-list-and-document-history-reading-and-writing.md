---
id: PLMCP-89
title: Complete the task list and document history, reading and writing
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:22:03Z'
updated_date: '2026-11-25T16:26:22Z'
labels: []
dependencies:
  - PLMCP-91
priority: medium
type: feature
ordinal: 70300
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The task list is in the admin toolset (off by default), beside user administration, although process_mail_account refers to it; the owner's agent did not see it. list_tasks, get_task, acknowledge_tasks (up to 100 ids) and run_task (three types) exist, get_document shows the newest 50 history entries, and get_instance_overview shows statistics and system status. POST /api/tasks/run/ takes one task type per call. Split from PLMCP-81. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Reading: active tasks, summary and status counts are readable
- [x] #2 list_tasks takes the API's remaining filters (dates, owner, trigger source, name, result, is_complete, ordering)
- [x] #3 Writing: acknowledge_tasks can acknowledge all, after a preview; every task type Paperless lets a user start can be started (confirm as in decision-26); that the API starts one type per call is stated, with one call per type as the closest way to start several
- [x] #4 A decision settles whether the task tools get a toolset that does not need user administration
- [x] #5 Document history older than the newest 50 entries is readable page by page, without OCR text
- [x] #6 The statistics needed for troubleshooting beyond get_instance_overview (task summary and status counts, and what else statistics and status give) are readable
- [x] #7 Every endpoint of tasks, document history, statistics and status in the vendored 3.5.1 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-91)
- [ ] #8 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each filter, the aggregates, acknowledge all, every startable type and paging of history
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
Cleanup 2026-11-25, criteria read literally (every item at every named level). All parts are merged (#167, #168, #169). Runs: full hosted E2E 45641757616 and CI 45641757981 of main 7fcce34 (with all three parts) green, the E2E in all ten jobs: tests/contract/test_task_lists.py 28 and tests/e2e/test_task_lists.py 13 passed (13 is every test of the file, no skip, so also test_all_tasks_are_acknowledged_after_a_preview and test_the_value_of_a_preview_is_stale_after_another_failure; by count, their names are not in the log). AC 3 is proven: acknowledging all (89.2), the three types started in E2E, one type per call stated in run_task. AC 8 stays open for what 89.1 (AC 5), 89.2 (AC 5) and 89.3 (AC 4) miss. DoD 1 and 3 are ticked in the parts; DoD 2: see the parts; DoD 4: merged through the parts' pull requests, no own one.
<!-- SECTION:NOTES:END -->
