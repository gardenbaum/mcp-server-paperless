---
id: PLMCP-89.3
title: 'Tasks c: get_system_report and history page by page'
status: To Do
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:40Z'
labels:
  - admin
dependencies:
  - PLMCP-89.1
parent_task_id: PLMCP-89
priority: medium
type: feature
ordinal: 106000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-89: history older than the newest 50 entries is not readable, and statistics and status give more than get_instance_overview shows. Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen', all as recommended; file design-plmcp-89.md of the main agent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Document history is readable page by page with history_page, without OCR text and within a size bound
- [ ] #2 get_system_report reads everything statistics and status give, without credentials, null where Paperless does not show it
- [ ] #3 Coverage: statistics, status and history have no open row; page and page_size of history are an owner-approved exclusion
- [ ] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the report, rights and paging of 60 changes
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
