---
id: PLMCP-89.1
title: 'Tasks a: toolset tasks, every filter of list_tasks, exclusions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:38Z'
updated_date: '2026-11-25T11:25:41Z'
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
- [ ] #2 list_tasks takes every filter and ordering of GET /api/tasks/, several states, types and sources, and rows show file name, owner and waiting time, nothing else of input_data
- [ ] #3 The probes of the design are run on Paperless 3.5.1 and their results recorded
- [ ] #4 Coverage: the filters are covered; GET /api/tasks/{id}/ and the ten task types Paperless refuses are owner-approved exclusions, quoted with their date
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each filter, the orderings, the moved toolset and the refused types
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
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
<!-- SECTION:NOTES:END -->
