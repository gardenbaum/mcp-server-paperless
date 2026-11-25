---
id: PLMCP-89.2
title: 'Tasks b: get_task_overview, acknowledge all, texts of run_task'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:39Z'
updated_date: '2026-11-25T16:27:20Z'
labels:
  - admin
dependencies:
  - PLMCP-89.1
parent_task_id: PLMCP-89
priority: medium
type: feature
ordinal: 105000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-89: how many tasks need attention, what runs now and how each type did is not readable, and acknowledging many failures takes ids in hundreds. Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen', all as recommended; file design-plmcp-89.md of the main agent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 get_task_overview reads status counts, active tasks and the summary, says what the filters and the rights apply to, and names refused parts
- [x] #2 acknowledge_tasks can acknowledge all after a preview and a yes bound to the counts and the newest task, and reports what slipped in
- [x] #3 run_task says that Paperless starts three types by hand, one per call, and what llm_index does as probed
- [x] #4 Coverage: active, summary and status_counts are covered; filters Paperless ignores are owner-approved exclusions
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the aggregates, acknowledge all as staff and user, and every startable type
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
1. Red: adapter, contract, service, tool and E2E for count_tasks, list_active_tasks, summarize_tasks, get_viewer, acknowledge_all_tasks, overview, preview, bound yes, lost answer.
2. Ports, adapter, service, tool get_task_overview, acknowledge_tasks with all; texts of run_task by P4.
3. decision-94, coverage, exclusions, guide limit +20, docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first (commit 'red'), then green. 34 hand mutants on a git archive copy: 2 survived; one was a redundant part of the scope (removed), one closed by a test. Deviations: a fourth GET (ui_settings) says whose tasks the summary counts and how many of those to acknowledge are not the user's own; a row of acknowledge by ids sends all=false. Limit: the guide +20 of the approved 89. Not proven: what came in between the second read and the POST on 3.5.1 (only on the fake).

Green: every check of #168; full hosted E2E of the stack 45618698199.

Review: M3 (at least), L1 to L4, L6, L8 and the Codex thread (a broken connection counts as a lost answer) fixed, tests first (14 red); 12 hand mutants, all killed. M2 (binding confirm=true for clients without dialogs) was put to the owner; AC 2 is not narrowed and is unchecked until it is built.

Owner, 2026-11-25, as relayed by the main agent: 'alles wie von dir empfohlen' (everything as you recommend). (1) The sizes of PLMCP-89 are accepted: tasks 21.2 KB, admin 31.2 KB, get_document +283 bytes, core +434 bytes. (2) M2 variant A is built: confirm=true for all_unacknowledged counts only with the preview's expected value. acknowledge_tasks 1,968 to 2,197 bytes (+229 of about 245). Red first at service and tool level (48 red), E2E written before its first hosted run; 14 hand mutants, all killed.

Cleanup 2026-11-25, criteria read literally (every item at every named level). Merged as 5dc35fa (#168). Runs: full hosted E2E 45641757616 and CI 45641757981 of main 7fcce34 (with all three parts) green, the E2E in all ten jobs: tests/contract/test_task_lists.py 28 and tests/e2e/test_task_lists.py 13 passed (13 is every test of the file, no skip, so also test_all_tasks_are_acknowledged_after_a_preview and test_the_value_of_a_preview_is_stale_after_another_failure; by count, their names are not in the log). AC 2 is proven: preview, a yes bound to counts and newest task (dialog, and confirm with the preview's value), what came in: tests/services/test_task_lists.py, tests/tools/test_tasks_toolset.py and E2E; what slips in between the second read and the POST is proven on the fake only. AC 5, missing: contract tests of starting train_classifier and llm_index; tool tests of what the summary by type holds, of acknowledging all as a user who is not staff, and of starting train_classifier and llm_index; E2E of acknowledging all as a user who is not staff. DoD 2 stays open: no local run of the whole just check is recorded, and the check jobs of the last head of the pull request were cancelled by its merge; those of CI 45641757981 (7fcce34) and 45646896491 (205e78f) are green.

Final summary of the implementer, moved here because the task is open: get_task_overview, acknowledging all after a preview with a bound yes, true texts of run_task (decision-94).

The missing tests are in PLMCP-120.
<!-- SECTION:NOTES:END -->
