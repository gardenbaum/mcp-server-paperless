---
id: PLMCP-79
title: 'Complete the PDF tools: inspect, insert, extract and remove, metadata per part'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:50Z'
updated_date: '2026-11-25T14:53:39Z'
labels: []
dependencies:
  - PLMCP-74
  - PLMCP-91
priority: medium
type: feature
ordinal: 68000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset pdf (off by default) already rotates, merges, splits, deletes pages, removes passwords and reprocesses; edit_pdf already picks, reorders, rotates single pages and groups pages into several outputs. Example need: one scan holds a contract and two payment slips. Limits of the API: page_count is the only page information, a merge always makes a new document, a split into several files cannot be a version, edit_pdf has one source document, and a split takes no metadata per part. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Reading: page count, page sizes, rotation and a page overview (with the renderer and page images of PLMCP-74, since the API gives only page_count) help decide where to split
- [x] #2 Writing: insert pages of one document into another; extract pages into a new document and keep them in or remove them from the source in one call; what happens to metadata, notes and custom fields is stated and live-tested
- [x] #3 An insert composed of several operations leaves the sources untouched if a step fails and names leftovers
- [x] #4 Every operation says before it runs what it produces and what is lost, and needs confirm where decision-6 requires it
- [x] #5 Operations with exactly one output file for one document can create a version instead of replacing; merge and multi-part split say why they cannot and name the closest way (new documents; merging the result as a version, which is final)
- [x] #6 After a split, title, type, correspondent, date and tags can be set per part; parts whose task is unfinished, failed or not visible are named with how to continue, and nothing is trashed before all parts exist
- [ ] #7 Every endpoint of PDF operations, with their options (for example source_mode and archive_fallback) in the vendored 3.5.1 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-91)
- [ ] #8 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each new operation, its preview and refusal, failed steps and tasks the user may not see
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
From PLMCP-74: the renderer is rendering/ (pypdfium2 and Pillow in a bounded child process, decision-42). get_document_pages reports page_count and shows pages; page sizes and rotation are not reported yet and can come from the same child (rendering/worker.py).

Design approved by the owner on 2026-11-24, all 14 decisions as recommended ('Bitte alles wie von dir empfohlen umsetzen.'). Split into 79.1 to 79.4, one pull request each.

Cleanup 2026-11-25: all parts (79.1 to 79.5) are merged and archived; CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. AC 7 stays open: from_webui of edit_pdf, merge, remove_password, reprocess and rotate is neither sent nor an owner-approved exclusion (open under PLMCP-83). AC 8 stays open: no E2E test has a failed step; the failed part on the duplicates stack named in the approved design is on the fake only (79.1: own products are never duplicates, so it cannot be forced that way). DoD 1 and 2 are open in the parts; DoD 4: merged through the parts' pull requests, no own one.

Read literally (2026-11-25), AC 8 misses: contract tests of extract_pages and of split with metadata as operations (preview, refusal, failed steps), of the preview, refusal and failed steps of insert_pages, and of the refusal of inspect_pdf; tool tests of tasks the user may not see for insert_pages, extract_pages and split_document, and of failed steps of extract_pages and split_document through the tools (tests/tools/test_pdf_next.py calls the service); E2E of failed steps of insert_pages, extract_pages and split_document and of tasks the user may not see for insert_pages and extract_pages.

Correction: the refusal of inspect_pdf is in a contract test (tests/contract/test_document_reader.py), so it is not missing.
<!-- SECTION:NOTES:END -->
