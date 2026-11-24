---
id: PLMCP-79.3
title: 'PDF tools c: metadata per part, safe replacing, extract_pages'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:16Z'
updated_date: '2026-11-24T13:19:24Z'
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
- [ ] #1 Split sets title, type, correspondent, date and tags per part; every part reports state, metadata and the next call
- [ ] #2 extract_pages keeps or removes the pages of the source; every failure row of the design is tested with its state
- [ ] #3 Nothing is trashed while a part is unfinished, failed, hidden, duplicate, stopped or without its metadata
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
<!-- SECTION:NOTES:END -->
