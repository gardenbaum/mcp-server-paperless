---
id: PLMCP-89.1
title: 'Tasks a: toolset tasks, every filter of list_tasks, exclusions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:38Z'
updated_date: '2026-11-25T14:04:04Z'
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
- [x] #1 list_tasks, acknowledge_tasks and run_task are in the opt-in toolset tasks, no longer in admin; a decision says why, and release notes, configuration and README name the break
- [x] #2 list_tasks takes every filter and ordering of GET /api/tasks/, several states, types and sources, and rows show file name, owner and waiting time, nothing else of input_data
- [x] #3 The probes of the design are run on Paperless 3.5.1 and their results recorded
- [ ] #4 Coverage: the filters are covered; GET /api/tasks/{id}/ and the ten task types Paperless refuses are owner-approved exclusions, quoted with their date
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each filter, the orderings, the moved toolset and the refused types
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
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

Review fixes: the pointers to list_tasks name the toolset (upload_documents and its unknown outcome; snapshot of upload_documents changed), superuser without staff pinned on fake and 3.5.1, every ordering in the adapter test, hashes of the two moved equivalent mutants recomputed with mutmut's own functions in-process (no run), line breaks in configuration.md.
<!-- SECTION:NOTES:END -->
