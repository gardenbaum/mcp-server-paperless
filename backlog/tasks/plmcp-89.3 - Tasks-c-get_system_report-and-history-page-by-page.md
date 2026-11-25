---
id: PLMCP-89.3
title: 'Tasks c: get_system_report and history page by page'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:40Z'
updated_date: '2026-11-25T12:34:20Z'
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
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: pages of the history, size bound, report models, planted credentials, contract, tool, E2E.
2. history_page on get_document (cut here), SystemReportReader, get_system_report.
3. decision-95, coverage, guide limit +20, docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first (commit 'red'), then green. 23 hand mutants on a git archive copy: 1 survived, closed by testing every text of the status instead of a sample. Deviation: the report is a port of its own (SystemReportReader), so that the readers of the overview in the tests stay. No open coverage row of PLMCP-89 is left. Guide limit +20, the last of the approved 89 (49 + 20 + 20).
<!-- SECTION:NOTES:END -->
