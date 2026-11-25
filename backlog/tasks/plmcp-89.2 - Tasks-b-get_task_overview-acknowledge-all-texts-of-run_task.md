---
id: PLMCP-89.2
title: 'Tasks b: get_task_overview, acknowledge all, texts of run_task'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:39Z'
updated_date: '2026-11-25T13:49:04Z'
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
- [x] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the aggregates, acknowledge all as staff and user, and every startable type
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
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
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
get_task_overview, acknowledging all after a preview with a bound yes, true texts of run_task (decision-94).
<!-- SECTION:FINAL_SUMMARY:END -->
