---
id: PLMCP-80
title: 'Complete bulk changes: individual values and selection by search'
status: To Do
assignee: []
created_date: '2026-11-23T12:14:50Z'
updated_date: '2026-11-25T14:46:43Z'
labels: []
dependencies:
  - PLMCP-91
priority: medium
type: feature
ordinal: 69000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset bulk (off by default) applies one operation with the same values to up to 100 documents named by id; different values need one update_document call each, and there is no bulk endpoint for that, so each document needs its own PATCH. Rotate, merge, split, edit and permissions are already covered by the pdf and permissions toolsets. Requested by the owner on 2026-11-23. Paperless 3.5.1 (PLMCP-97) added the bulk_edit method run_workflow (it runs a workflow with a manual trigger on the selected documents; see PLMCP-87) and `from_webui` on the delete request (the web UI sets it to keep its selection). Every queued document operation now answers a task_id, which the adapter already follows.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 One call changes many documents with individual values each, every field update_document knows, with a dry run, confirmation and a result per document; a failure names what was and was not changed; owner changes keep decision-12's confirm
- [x] #2 Documents can be chosen by ids, by the search filters or by a saved view, up to a stated maximum, with the matched set shown first and protected against changes in between
- [x] #3 Reading many documents in one call is possible (fields selectable) within a size limit; content only on request (decision-4 keeps OCR text out of list rows)
- [x] #4 Notes can be added to many documents in one call, with a result per document
- [x] #5 Every endpoint of bulk operations (every bulk_edit and bulk_edit_objects method) in the vendored 3.5.1 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-91)
- [ ] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover limits, rate, partial failures, a changed set between preview and confirm and a restricted user
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Cleanup 2026-11-25: all parts (80.1 to 80.4) are merged and archived; CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. AC 6 stays open: the rate (HTTP 429 ends a bulk call and sends nothing more; design section 7) is tested on adapter and service level only, in no contract, tool or E2E test. Limits, partial failures, a changed set and a restricted user are covered. AC 5: from_webui of bulk_edit and delete stays a field of PLMCP-83 (decision-72); every endpoint and method is covered or excluded. DoD 1 and 2 are open in the parts; DoD 4: merged through the parts' pull requests, no own one.
<!-- SECTION:NOTES:END -->
