---
id: PLMCP-89
title: Complete the task list and document history, reading and writing
status: To Do
assignee: []
created_date: '2026-11-23T12:22:03Z'
updated_date: '2026-11-23T12:25:17Z'
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
- [ ] #1 Reading: active tasks, summary and status counts are readable
- [ ] #2 list_tasks takes the API's remaining filters (dates, owner, trigger source, name, result, is_complete, ordering)
- [ ] #3 Writing: acknowledge_tasks can acknowledge all, after a preview; every task type Paperless lets a user start can be started (confirm as in decision-26); that the API starts one type per call is stated, with one call per type as the closest way to start several
- [ ] #4 A decision settles whether the task tools get a toolset that does not need user administration
- [ ] #5 Document history older than the newest 50 entries is readable page by page, without OCR text
- [ ] #6 The statistics needed for troubleshooting beyond get_instance_overview (task summary and status counts, and what else statistics and status give) are readable
- [ ] #7 Every endpoint of tasks, document history, statistics and status in the 3.4 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-91)
- [ ] #8 Contract tests on the fake and Paperless 3.4.2, tool tests and E2E cover each filter, the aggregates, acknowledge all, every startable type and paging of history
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
