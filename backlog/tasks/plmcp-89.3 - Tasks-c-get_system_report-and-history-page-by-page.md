---
id: PLMCP-89.3
title: 'Tasks c: get_system_report and history page by page'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T11:24:40Z'
updated_date: '2026-11-25T16:26:21Z'
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
- [x] #1 Document history is readable page by page with history_page, without OCR text and within a size bound
- [x] #2 get_system_report reads everything statistics and status give, without credentials, null where Paperless does not show it
- [x] #3 Coverage: statistics, status and history have no open row; page and page_size of history are an owner-approved exclusion
- [ ] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the report, rights and paging of 60 changes
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
1. Red: pages of the history, size bound, report models, planted credentials, contract, tool, E2E.
2. history_page on get_document (cut here), SystemReportReader, get_system_report.
3. decision-95, coverage, guide limit +20, docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first (commit 'red'), then green. 23 hand mutants on a git archive copy: 1 survived, closed by testing every text of the status instead of a sample. Deviation: the report is a port of its own (SystemReportReader), so that the readers of the overview in the tests stay. No open coverage row of PLMCP-89 is left. Guide limit +20, the last of the approved 89 (49 + 20 + 20).

Full hosted E2E 45618698199 on 9c536a1; tests/contract/test_document_reader.py 33, test_instance_reader.py 15 live. The audit log switched off is tested on the fake and in the adapter only.

Review: the hint for a too large history comes from its caller (L5), the port names the 60 s wait; equivalent mutants of DocumentService.detail renumbered (one parameter more, checked against mutmut's generated source, not run).

Cleanup 2026-11-25, criteria read literally (every item at every named level). Merged as 58c792a (#169). Runs: full hosted E2E 45641757616 and CI 45641757981 of main 7fcce34 (with all three parts) green, the E2E in all ten jobs: tests/contract/test_task_lists.py 28 and tests/e2e/test_task_lists.py 13 passed (13 is every test of the file, no skip, so also test_all_tasks_are_acknowledged_after_a_preview and test_the_value_of_a_preview_is_stale_after_another_failure; by count, their names are not in the log). AC 4, missing: a contract test (fake and 3.5.1) of paging 60 changes of the history; the contract test makes 3 and reads no pages. Report and rights are covered at every level, paging in tool tests and E2E. DoD 2 stays open: no local run of the whole just check is recorded, and the check jobs of the last head of the pull request were cancelled by its merge; those of CI 45641757981 (7fcce34) and 45646896491 (205e78f) are green.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
history_page on get_document and get_system_report (decision-95); no open coverage row of PLMCP-89.
<!-- SECTION:FINAL_SUMMARY:END -->
