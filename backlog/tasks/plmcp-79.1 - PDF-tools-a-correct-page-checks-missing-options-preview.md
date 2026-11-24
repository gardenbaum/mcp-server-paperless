---
id: PLMCP-79.1
title: 'PDF tools a: correct page checks, missing options, preview'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:14Z'
updated_date: '2026-11-24T09:23:08Z'
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
<!-- SECTION:NOTES:END -->
