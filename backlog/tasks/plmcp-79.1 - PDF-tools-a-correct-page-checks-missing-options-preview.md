---
id: PLMCP-79.1
title: 'PDF tools a: correct page checks, missing options, preview'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:14Z'
updated_date: '2026-11-24T10:21:40Z'
labels: []
dependencies: []
parent_task_id: PLMCP-79
priority: medium
type: feature
ordinal: 86000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First of four parts of PLMCP-79 (design approved by the owner on 2026-11-24 (all 14 decisions as recommended); file design-plmcp-79.md of the main agent, section 9). Fix T1 (pages are checked against the first file), the archive serial number text of remove_document_password, version_id, use_original_files, keep_metadata, archive_fallback, merge omission check, dry_run and plan on every writing tool; the server trashes originals last (merge, remove_document_password).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A document whose newest version is longer than its first file can be edited on every page, shown on 3.5.1
- [ ] #2 Every writing pdf tool has dry_run and a plan; confirmations bind what the design lists
- [ ] #3 The options of the design are sent; the owner-approved exclusions are in the coverage table with their date
- [ ] #4 No original is trashed before every product was verified, also for merge and password removal
- [ ] #5 Probes of the design became contract tests; fake, 3.5.1, tool and E2E tests cover it
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
1. Probe module (P1 to P8, P11) on GitHub; facts into the notes.
2. T1: the adapter takes the version a request reads (domain PageSource), the fake gets a page count per version; contract tests both ways.
3. version_id, use_original_files, keep_metadata, archive_fallback through domain, port, adapter, fake, service, tools.
4. Merge omission check before the request, page count check after it.
5. plan and dry_run on every tool; confirmation scopes.
6. Trashing last by this server after every product was verified (split, delete_pages, edit_pdf, merge, remove_password); decision-60; coverage rows.
7. Probes become contract tests, probe module deleted; E2E; docs, snapshot.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Owner approval (2026-11-24)

Presented to the owner as numbered decisions 1 to 14 with the recommendations above (1 B, 2 four pull requests with a Backlog task each, 3a incl. merge and remove_document_password, 4 no, 5a, 6a incl. delete_original(s), 7 limit 2,880, 8 dry_run, 9 contact sheets, 10 first option, 11 yes, 12 without finish_split, 13 skip, 14 PDFs only). Owner: "Bitte alles wie von dir empfohlen umsetzen." Excluded parameters (6) are thereby owner-approved exclusions of 2026-11-24.
Note: PLMCP-101 raised DEFINITION_ADDED_LIMIT to 879; PLMCP-78 takes decisions 54/55, 101 takes 57; decisions of 79 start at 60.

Probe facts on 3.5.1 (run 45361040241 and CI 45361045282, 2026-11-24):
- P1: GET ?version=<id> gives that version's page_count (root 2, newest 4); metadata takes version. edit_pdf page 4 with the root id: 400 "out of bounds for document with 2 pages"; with the version id 200, product titled after the root, filename {root}_edit_1.pdf; owner without superuser and a user on an unowned document may send a version id, a change grant alone is 403.
- P2: root id + explicit_selection reads the first file (edit, rotate, merge, new version). Root id + latest with a page only the first file has passes the form and the bulk_update task fails.
- P3: delete_original with a version id trashes only that version.
- P4: products differ in bytes every time, so a part is never a duplicate of an earlier one, also with PAPERLESS_CONSUMER_DELETE_DUPLICATES. A rejected upload's task row is "failure". So T2 is not reached through duplicates of own products; a stopped consumption stays read in the source ([R]).
- P5: kept: no ASN; delete_original with one output hands it over; two outputs: cleared everywhere; merge hands over the metadata document's; remove_password: not handed over, the trashed original keeps it, "already exists in the trash" (400) for others, restoring brings it back.
- P6: include_metadata=false: title is the file name ({id}_edit_1), no tags but inbox tags (both forms get inbox tags), no metadata. A new version with include_metadata=false keeps everything of the document, text is read again (correction gone from the shown text). A rotated unowned document keeps no owner. A merge of a PDF and a text without fallback leaves the text out: 92_merged.pdf, title 92_merged.
- P7: parts are {id}_edit_{n}.pdf with ascending task ids; without view_paperlesstask tasks are 403; with it a user's own tasks are listed.
- P8: images need a dpi to be consumed; asked again in a second probe.
- P11: edit and merge products keep pages and annotations, lose bookmarks, attachments, the form (its logic) and signatures; a rotated file keeps bookmarks, attachments, form and the signature field.
- SURPRISE (new trap): editing a document with every custom field kind with include_metadata made document 94 but NO consume_file task row ever appeared (bulk_update succeeded). Second probe asks which field kind hides it.

Second probe (CI 45364769800, 2026-11-24):
- Only a DATE among the custom fields hides the consume_file task of everything that copies the metadata (edit into new documents, rotate, merge with a metadata document); string, url, boolean, integer, float, monetary, documentlink, select and longtext do not. The document is made all the same. Source: the JSON of the task row cannot hold the nested date, the error is logged and swallowed.
- Product with metadata: title, created, correspondent, type, storage path, tags, all custom fields and grants; no notes, no share links, no correction of the text; a document link is copied, the linked document gets no back link.
- P8: an encrypted PDF and a text file are left out with and without archive_fallback, an image only without; an image's document has no page count, its archive PDF has one page. The file name lists what is in the merge. Only an unreadable document: no task. With delete_originals only the included ones go.
Probe module deleted; the facts are contract tests in tests/contract/test_pdf.py.

Design choices: merge with use_original_files and delete_originals is refused where a document shows a newer file (as version_id with replace_original); keep_metadata=false with new_version is refused; versions are always requested with include_metadata=false (no effect on the document, keeps the task visible); rotate takes confirm and stays non-destructive; trash-last covers split, delete_pages and edit_pdf here too (AC 4), so 79.3 adds part metadata on top of services/pdf_replacing.py.
Deviation: code and tests of the adapter, service and tool steps were written together, the tests not run red before the code in every case.
Not covered as designed: the failed part on the duplicates stack in E2E, because own products never are duplicates (P4); it is covered on the fake.
Snapshots and limits: surface snapshot, docs/tools.md, docs/api-coverage.md regenerated (new parameters, plan, texts); no limit or budget raised. Contract tests of delete_original(s) removed with the port option. test_arguments.py names the options its pdf sweeps vary, because dry_run sends nothing.
Local: ruff and basedpyright clean, targeted tests of the touched modules green, diff-cover 100 %; no full suite, E2E or live run.

Contract tests removed (tests/contract/test_pdf.py), because the port no longer offers Paperless's own deletion (owner decision 3a and 6a, decision-60), so there is nothing left to contract:
- test_merging_can_send_the_originals_to_the_trash
- test_an_edit_can_replace_the_original_by_trashing_it
- test_a_password_can_be_removed_while_trashing_the_locked_original
- test_deleting_originals_needs_the_right_to_delete_them
Replaced by tests/services/test_pdf_replacing.py (every way a replacement ends, refused deletion included) and tests/e2e/test_pdf.py; delete_document itself keeps its contract tests. Adapter tests removed for the same reason: test_a_delete_of_originals_adds_a_task_that_is_not_reported, test_an_edit_that_replaces_the_original_asks_for_its_deletion, and the replace_original case of test_removing_a_password_sends_it_with_where_the_copy_goes.

Merged plmcp-78-trash (a415807): its TRASHED/TRASHED_EACH notes ("once the work succeeds") give way to the message of pdf_replacing ("Now in the trash: ...", WAY_BACK) after the originals went; the plan of a replacing call says the way back too. Its test test_whatever_trashes_an_original_says_how_to_get_it_back asserts the new words. OVERVIEW_GUIDE_LIMIT 2835 and DEFINITION_ADDED_LIMIT 879 unchanged; snapshot, docs/tools.md and docs/api-coverage.md regenerated.

Review of 5307caa (0 Critical, 5 Important, 9 Minor), all fixed on top of the merge, tests seen red first:
I1 number goes back when its own original stays, or its place is told; delete right checked beforehand (new port method PdfWriter.may_delete). I2 next carries its conditions and never suggests a deletion for a missing, hidden or doubtful product. I3 StartedTasks.doubt: window of the queued task, exact number of files, copy flag, no other copying operation of the user at the same time. I4 clock and re-read before each deletion; a lost answer is looked up. I5 both causes for unlocking; live contract for the hidden tasks of rotate, merge and unlock.
M1 to M9 as asked; texts of confirmations and plans changed on purpose (exact-word tests updated): numbers are named only where there are any. The hint of a PDF without page count no longer claims encryption.
Not fixable: an upload of the same user named like a part, in the window, with the copy flag, in place of a hidden file (decision-60, consequences).

Correction: the new port method is DocumentReader.may_delete (a read). The case split/other_pages of test_confirmation_scope.py is gone: the pages that get lost are in the text of the confirmation now, so it tells them apart itself.

Second correction: that case is not gone but uses delete_pages as a new version, whose text does not tell the page counts apart.

Delta review of fd3bab2 fixed, tests seen red first: N1 no number of others is overwritten, N2 a left-out document is named first, N3 a deletion without an answer is not known, N4 merge confirmation names the metadata loss, N5 users without view_uisettings (decision-60, plan, confirmation), N6 the way back in confirmations and the not-known note. Exact-word tests changed for that.

Security finding on N5 fixed, tests seen red first: rights that could not be read keep the originals for that reason (Replacement.unread), also after a transient failure, and are part of the confirmation scope.

Last review of 9d94025 fixed, tests seen red first: next never overwrites a number the new document has; own condition per original after an unknown deletion.
<!-- SECTION:NOTES:END -->
