---
id: PLMCP-89.1
title: 'Tasks a: toolset tasks, every filter of list_tasks, exclusions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:38Z'
updated_date: '2026-11-25T16:27:19Z'
labels:
  - admin
dependencies: []
parent_task_id: PLMCP-89
priority: medium
type: feature
ordinal: 104000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-89: the task tools are in admin, beside user administration, so an agent that should look after imports needs the toolset that manages users; list_tasks takes three of the filters Paperless has. Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen', all as recommended; file design-plmcp-89.md of the main agent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 list_tasks, acknowledge_tasks and run_task are in the opt-in toolset tasks, no longer in admin; a decision says why, and release notes, configuration and README name the break
- [x] #2 list_tasks takes every filter and ordering of GET /api/tasks/, several states, types and sources, and rows show file name, owner and waiting time, nothing else of input_data
- [x] #3 The probes of the design are run on Paperless 3.5.1 and their results recorded
- [x] #4 Coverage: the filters are covered; GET /api/tasks/{id}/ and the ten task types Paperless refuses are owner-approved exclusions, quoted with their date
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each filter, the orderings, the moved toolset and the refused types
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
1. Probes P1 to P6 on Paperless 3.5.1 in a draft PR (temporary module, deleted afterwards).
2. Red first: adapter (repeated values, every filter and ordering), contract on fake and live, service, tool; then new modules domain/task_lists.py, paperless/task_lists.py, services/task_lists.py, tools/tasks.py with the ports TaskListReader and TaskListWriter, moved out of admin.
3. Toolset tasks: registry tables, server, guide limit +49 of the approved +89, configuration, README, release notes draft.
4. decision-93, coverage rows and exclusions, E2E.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval of 2026-11-24: 'Alle wie empfohlen' (all as recommended), for the design with every recommended option.

Probes P1 to P6: CI run 45604535090, job 139171854815 (e2e 1/6), all as the design says; recorded in decision-93 (P1 list, P2, P6), 94 and 95. Guide limit 2987 to 3036 (49 of the approved 89). 23 hand mutants on a git archive copy, 2 survived first and were closed by tests. Deviation: the ports are TaskListReader and TaskListWriter instead of methods of the admin ports; rows show the owner as id. #161 (explain_workflow) calls AdminReader.list_tasks with a time window: at the merge it takes TaskListReader.list_tasks(TaskFilters(...)) with created_from and created_to.
Release notes draft: BREAKING: list_tasks, acknowledge_tasks and run_task moved from the toolset admin to the new toolset tasks. If PAPERLESS_MCP_TOOLSETS names admin, add tasks (all needs no change). list_tasks takes every filter and ordering of Paperless.

Green on d4f9fcf: every check of #167. Full hosted E2E of the stack 45618698199 (9c536a1): tests/contract/test_task_lists.py 26, tests/e2e/test_task_lists.py 12 passed; only e2e-imap red, on a test that is red on main too (runs 45609667829, 45615009952).

The E2E tests were written before their first hosted run and not run red (no local E2E). The README names the break with 89.3's commit.

Review fixes: the pointers to list_tasks name the toolset (snapshot of upload_documents changed), superuser without staff pinned on fake and 3.5.1, every ordering in the adapter test, hashes of the moved equivalent mutants recomputed with mutmut's own functions in-process (no run), line breaks in configuration.md.

Merged main 1efb5cd (106 tools, guide 3069). explain_mail_import and explain_workflow read TaskListReader; AdminReader.list_tasks and the created= window with its tests are gone. Output change of explain_mail_import: failed files are counted exactly by trigger source. Guide measured 3118, 3138, 3158 (3069 + 89), instructions 1443.

Cleanup 2026-11-25, criteria read literally (every item at every named level). Merged as de956f5 (#167). Runs: full hosted E2E 45641757616 and CI 45641757981 of main 7fcce34 (with all three parts) green, the E2E in all ten jobs: tests/contract/test_task_lists.py 28 and tests/e2e/test_task_lists.py 13 passed (13 is every test of the file, no skip, so also test_all_tasks_are_acknowledged_after_a_preview and test_the_value_of_a_preview_is_stale_after_another_failure; by count, their names are not in the log). AC 1 stays open: configuration and README name the break, the release notes do not exist yet (no release after v0.4.0; a draft is in these notes and in #167). AC 5, missing: contract tests of the moved toolset; tool tests of the orderings created, finished (ascending), task_type, duration_seconds and wait_time_seconds both ways and state descending, and of the refused types of run_task; E2E of the orderings other than -finished and of the refused types. acknowledged and result of list_tasks are sent in a tool test (test_arguments), their effect is in contract and E2E. DoD 2 stays open: no local run of the whole just check is recorded, and the check jobs of the last head of the pull request were cancelled by its merge; those of CI 45641757981 (7fcce34) and 45646896491 (205e78f) are green.

Final summary of the implementer, moved here because the task is open: The task tools have the toolset tasks; list_tasks takes every filter and ordering; exclusions recorded (decision-93). Probes P1 to P6 done.

The missing tests are in PLMCP-120; the release notes of AC 1 belong to the task of the 0.5.0 release. Read literally, AC 5 also misses tool tests of the effect of acknowledged and result, and E2E that assert an order (the one with -finished has a single task).
<!-- SECTION:NOTES:END -->
