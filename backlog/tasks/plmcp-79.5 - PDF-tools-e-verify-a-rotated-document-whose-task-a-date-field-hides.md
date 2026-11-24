---
id: PLMCP-79.5
title: 'PDF tools e: verify a rotated document whose task a date field hides'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T13:42:49Z'
updated_date: '2026-11-24T18:35:27Z'
labels:
  - pdf
dependencies:
  - PLMCP-79.3
  - PLMCP-79.4
parent_task_id: PLMCP-79
priority: low
type: enhancement
ordinal: 96000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A date custom field hides the consume_file task of a rotated document, so rotate_documents can only answer unknown. The newest version before and after tells whether the rotation is done. Owner approval of 2026-11-24: own task after PLMCP-79.4.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 rotate_documents reports a finished rotation also when a date field hides its task, by comparing the newest version before and after
- [ ] #2 A version made by somebody else meanwhile is never taken for the rotation
- [ ] #3 Contract, service and tool tests
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
1. Port PdfWriter.find_rotations(request, before): versions newer than the one the document showed before the request, taken only with the evidence Paperless 3.5.1 gives: added since the queued task started, original file name {id}_rotated.pdf, 'Version Added' history entry by the calling user, no other rotation or merge-as-versions of this user at once or pending, no listed task of that file name, exactly one such version. No history or any doubt: unknown.
2. Adapter (pdf.py), fake, contract (fake and live), red first.
3. Parts.turned (the wait loop of look is shared), PdfService.rotate uses it for the documents whose own date hides their task; mixed requests keep the listed tasks.
4. Service, tool and E2E tests; decision that supplements decision-67; docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented, test first at adapter, contract (fake), service and tool level (each run red before the code); E2E and live contract run on GitHub only.
Evidence a version is taken on: newer than the one shown before; added since the queued task started; file name {id}_rotated.pdf; history entry 'Version Added' by the calling user; the only one; no other rotation of the user at once, pending or unsettled in the last hour; no unfinished listed task of that name. No history (view_logentry, audit log off) or any doubt: unknown (decision-74).
Deliberate: surface snapshot +2 lines (one sentence in the description of rotate_documents). Decision-74 supplements decision-67 and replaces its consequence that rotating is not covered. No limit, gate or threshold changed. FakePaperless now keeps file names of versions and writes 'Version Added' history entries, as 3.5.1 does.
Local: changed lines 100 % covered, 16 hand mutations of the conditions killed. tests/quality/test_backlog_instructions.py::test_the_menu_driver_reports_a_failing_cli failed once locally (pty EIO, untouched code).
<!-- SECTION:NOTES:END -->
