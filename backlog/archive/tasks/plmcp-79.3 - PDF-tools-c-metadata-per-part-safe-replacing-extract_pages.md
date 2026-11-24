---
id: PLMCP-79.3
title: 'PDF tools c: metadata per part, safe replacing, extract_pages'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:16Z'
updated_date: '2026-11-24T15:13:38Z'
labels: []
dependencies:
  - PLMCP-79.1
parent_task_id: PLMCP-79
priority: medium
type: feature
ordinal: 88000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-79 (design approved by the owner on 2026-11-24 (all 14 decisions as recommended); file design-plmcp-79.md of the main agent, sections 2.3, 2.4).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Split sets title, type, correspondent, date and tags per part; every part reports state, metadata and the next call
- [x] #2 extract_pages keeps or removes the pages of the source; every failure row of the design is tested with its state
- [x] #3 Nothing is trashed while a part is unfinished, failed, hidden, duplicate, stopped or without its metadata
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Hidden products (owner ruling of 2026-11-24): PdfWriter.find_copies finds the documents of a request whose tasks a date custom field hides by file name; adapter, fake, contract (fake and 3.5.1), doubt rules.
2. PartMetadata resolved before the start, parts verified and patched one by one through DocumentEditor (services/pdf_parts.py); split_document part_metadata; parts[] and original in the result; replacing waits for metadata.
3. extract_pages: new document, verify, metadata, re-read, new version of the rest; every failure row of the design.
4. Plan, confirmation texts and scopes, decisions, annotations, budgets, coverage table, README, docs/tools.md, snapshots.
5. E2E and live contract tests, read from CI; draft PR.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Owner approvals

Design of PLMCP-79, 14 decisions as recommended (2026-11-24): "Bitte alles wie von dir empfohlen umsetzen." Used here: 2 (a pull request of its own), 3a (this server trashes last), 4a (no trashing while metadata of a part is not set), 7 (guide limit at most 2880 for three names), 8 (dry_run), 12 (no finish_split).
Owner ruling for 79.3 (2026-11-24): "das neue Dokument über den Dateinamen finden und prüfen" (date custom field hides the task).

## What was built

- services/pdf_parts.py: metadata resolved before the start, parts verified one by one, set through DocumentEditor, look by file name. PdfWriter.find_copies (port, adapter, fake, contract). extract_pages and the removal in services/pdf.py. pdf_replacing.py: checked, verified, unchanged are shared; a missing metadata keeps the original. decision-67 (67 from the CLI; 66 is on plmcp-112-webhooks).
- Result: parts[], original (kept, trashed, shortened, unknown); PdfTask.task_id may be null (found by file name).

## Deviations from the design

- The search by name costs about 95 lines of adapter, not about 40: Paperless does not say which document a queued operation worked on (input_data holds the operation and a count only), so an earlier request of the same document whose file still waits for OCR cannot be told by one row. Every copying operation of the user in the last hour must be known to have made something else. Otherwise the new documents stay untouched.
- The search also covers merge and remove_document_password (the ruling says edit_n.pdf etc.), not rotate (a version, replaces nothing).
- PartOutcome.state is the state of the task; an unverified success says why in message. The metadata PATCH counts as set only if the answer holds what was asked for.
- A refused metadata name refuses the whole call; tags is the whole list and so also takes the inbox tags away.
- find_copies reads but belongs to the writer port, so the fake guards it as a write (the existing rule of test_every_writer_method_of_the_fake_is_guarded).

## Deliberate changes

OVERVIEW_GUIDE_LIMIT 2849 to 2865 (16 bytes of extract_pages; 15 left for insert_pages). Surface snapshot, docs/tools.md, docs/api-coverage.md regenerated. test_every_writing_pdf_tool_has_a_preview: 8 tools. tests/services/test_pdf_replacing.py: test_a_document_with_a_date_field_keeps_the_original_and_says_why became test_documents_whose_tasks_a_date_hides_are_found_by_name_and_replace_the_original (the owner ruling changes the behaviour) and test_without_a_wait_nothing_is_looked_for; unlocking with a date is asked with wait_seconds=0.

## Evidence

Red first: tests/adapter/test_pdf_copies.py, tests/services/test_pdf_parts.py (collection error: the names did not exist), the tool tests (6 failed), the 30 second bound (2 failed). Not red first: the contract and E2E tests (written with or after the fake and the code) and the tests of the search by name in test_pdf_replacing.py. Changed lines of src covered 100 % by the targeted modules (diff-cover against the merged 79.2). ruff, basedpyright, import-linter clean. Not run locally: just check, full suite, E2E, mutation. tests/quality/test_backlog_instructions.py::test_the_menu_driver_reports_a_failing_cli fails locally (pty: Errno 5), which this change does not touch.

Review of 9a3985b (0 Critical, 3 Important, 9 Minor), owner 2026-11-24 "Bitte wie von dir empfohlen.": the one-hour rule is kept, tags is the whole list, rotate stays out of the name search for now. Fixed, tests seen red first: I1 unexpected errors after the start (Parts._finish, the removal, an outer guard in PdfService._run; the log has the type alone; CancelledError passes), I2 tags exactly the wanted ones and their parents, I3 the document must show the produced version with the remaining pages, M1 PENDING is waited for, M2 earlier operations are read once every file is there or at the end, unlocking a readable file looks for at most 5 seconds, M3 the order of pages is in text, fingerprint and parts, M5, M6, M7, M8 (the test of an unreadable field list changed on purpose). M9: tests/tools/test_pdf_next.py (written after the fixes), the adapter archive honours iendswith, the fake knows TASKED and the hour. The harness taxonomy ttl is 3600 again: the change to 0 was not needed. Not done, the owner's: a bound on tags per part (M4), remembering requests, rotate. merge_documents, outside _run, has no outer guard.

The outer guard (_guarded) now covers merge_documents and rotate_documents too, as the same helper as _run; red first (4 tests). remove_document_password, delete_pages, edit_pdf, split and extract go through _run. reprocess_documents has no such hole: after its start it only follows tasks (follow catches everything) and copies nothing. This replaces the earlier note that merge has no guard.

Delta review of b1f34da fixed, tests red first (N1 to N8): N1 the 5 second bound of unlocking is removed (it shortened an owner-ruled result), N2 a stopped call names every original, what is already in the trash and the task of the removal, N3 a part that cannot be checked does not stop the others, N4 lookups that break after the POST are problem failed, N5 and N6 the message of a stopped call, N7 test_pdf_next validates the arguments against the schemas and covers the guard (not red first; nor the last look of the search), N8 the condition covers both calls. test_pdf_next sets the switches of the fake directly: the setattr was banned by hygiene.

Live failure of 9a3985b (full E2E 45402232899, shard 1/4): a new user's first request was in doubt (PENDING). Cause: Paperless queues bulk_update_documents after metadata changes with the type bulk_update, no owner, empty input, trigger source system (source of 3.5.1: signals/handlers.py, bulk_edit.py, tasks.py, serialisers.py, views.py, workflows/actions.py: its only file work is unlocking as a version). Every user who may view tasks sees these rows, and the adapter counted them as operations of the user, so anybody's metadata change in the last hour left everyone in doubt. Only rows with exactly this shape are left out now; a row with an owner, an input or another trigger still counts. Red first: 3 adapter tests; the 4 near misses stay a doubt. The contract test makes such a row itself. The third failure was a wrong fixture password in the test.

Live rows (diagnostic run 45410381343, removed again): the rows counted against a fresh user were bulk_update rows with a named operation and owner null, of users deleted since (PaperlessTask.owner is SET_NULL); no row with empty input was among them, so the earlier note named the wrong cause. Prepared as option A, pending the owner's answer before the merge: in the search by name an operation is the caller's only if the caller owns it. Source of 3.5.1: all bulk views use IsAuthenticated (basic, token, session, remote user and auto login all give a User row), _queue_bulk_operation records request.user.pk, execute_bulk_operation and the consumer load that user by id (a deleted one raises), a deleted user's documents have owner null. The exclusion of the empty system rows is covered by it and gone; the doubt of listed tasks (decision-60) is as in 79.1. Red first: 12 adapter cases. copies() of the contract tests polls on a passing doubt.

Option A approved by the owner on 2026-11-24 ("alles wie empfohlen"); decision-67 says it and its limits. The contract test reads the row of the deleted user (named operation, owner null) and that the same-named document is not taken. A wait for the queued task that breaks hands the task back (unconfirmed), red first. CI 45415118286 (check 3.14 and 3.15, e2e tier) and full E2E 45415099254 of a386d6f green. DoD 1 stays open: the contract and E2E tests, the first service tests of the name search, test_pdf_next.py and the test of the fake were not seen red first. DoD 2 stays open: just check was not run locally (ruling: not locally), CI is green. DoD 4 stays open until the merge.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
split_document sets title, type, correspondent, date and tags per part; every part reports state, metadata and the next call. extract_pages (new) puts pages into a new document and with remove takes them out of the source as a checked new version. Originals go, and pages vanish, only when every new document is verified and has its metadata. Where a date custom field hides the tasks, new documents are found by file name and verified (find_copies, owner ruling; one-hour rule; operations the caller does not own do not count, option A). Unexpected errors after a start are not raised: the result says what is known and where to look. decision-67; OVERVIEW_GUIDE_LIMIT 2865 of the approved 2880; snapshots and docs regenerated. Evidence: failure tables on the fake, contract and E2E tests on Paperless 3.5.1 (full E2E 45415099254 and CI 45415118286 green on a386d6f); the changes after it are tests, the fake, decision text and the handling of a wait that breaks. Risks: see decision-67 (hand-over of a deleted user's documents, a backlog over an hour).
<!-- SECTION:FINAL_SUMMARY:END -->
