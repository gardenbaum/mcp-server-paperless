---
id: PLMCP-79.5
title: 'PDF tools e: verify a rotated document whose task a date field hides'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T13:42:49Z'
updated_date: '2026-11-24T21:03:32Z'
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
- [x] #1 rotate_documents reports a finished rotation also when a date field hides its task, by comparing the newest version before and after
- [x] #2 A version made by somebody else meanwhile is never taken for the rotation
- [x] #3 Contract, service and tool tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
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

Independent review of 206f5d3, all points fixed test first: (1) what could be this request's own, on any of its documents, settles no earlier rotation; (2) nor does a version of a document with a listed upload under that name; (3) one list of versions a look, file names and histories read once (history only when every version is there), a look is cut at the bound, one last look of at most 10 s, the time taken is reported; (4) a started rotation without end has its own sentence, no time limit because none can be proven safe (decision-74); (5) rotations that ended while a counted one ran are counted; (6) the race of the last read is closed by (1). The fake models unsettled earlier rotations; contract tests are exact. Mutants by hand: at the first pass 5 survived (first-file check, own row among the operations, two boundaries, the cut at the bound); tests were added and all five are killed now. The first round's 16 left one out (it hit another function); killed, too.

Second review of 9fef620. Correction of the note above: point (6) was NOT closed by (1); a rotation of another document, or an upload, that came while the versions were counted could settle an earlier rotation. Now tasks and operations are read again after the changed documents, and whatever came meanwhile is a doubt. Also: on every requested document nothing newer than the version shown before counts (clocks of two hosts); hidden and before are fields of RotationSearch. Operation None is no rotation any more: in the 3.5.1 source only execute_bulk_operation gives a bulk_update row an owner, always with operation and document_count (before_task_publish_handler); a rotation without a count stays unsettled. Surface snapshot: one more sentence in rotate_documents (10 seconds).

Live evidence: full hosted E2E 45465564080 (9fef620) and 45470371956 (9952784) green. Three review rounds, the last without a code finding. The restrictions of decision-74 are presented to the owner; main with PLMCP-80.3 merged.

Owner, 2026-11-24: "Alles wie von dir empfohlen." The ten restrictions are accepted. Merged in #144; full hosted E2E 45475365377 on the merged head: shard 1 of 4 was cancelled by the 25-minute guard without a failed test, all other parts green; the same tests were green on 9952784.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
rotate_documents finds the version of a rotation whose task a date field hides (decision-74).
<!-- SECTION:FINAL_SUMMARY:END -->
