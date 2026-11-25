---
id: PLMCP-89.2
title: 'Tasks b: get_task_overview, acknowledge all, texts of run_task'
status: To Do
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:39Z'
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
- [ ] #1 get_task_overview reads status counts, active tasks and the summary, says what the filters and the rights apply to, and names refused parts
- [ ] #2 acknowledge_tasks can acknowledge all after a preview and a yes bound to the counts and the newest task, and reports what slipped in
- [ ] #3 run_task says that Paperless starts three types by hand, one per call, and what llm_index does as probed
- [ ] #4 Coverage: active, summary and status_counts are covered; filters Paperless ignores are owner-approved exclusions
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the aggregates, acknowledge all as staff and user, and every startable type
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
