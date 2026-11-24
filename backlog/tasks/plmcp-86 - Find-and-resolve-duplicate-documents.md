---
id: PLMCP-86
title: Find and resolve duplicate documents
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:21:59Z'
updated_date: '2026-11-24T03:04:40Z'
labels: []
dependencies:
  - PLMCP-77
priority: high
type: feature
ordinal: 64200
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless keeps duplicates and knows them (duplicate_documents, filter has_duplicates), but no tool lists or resolves them; only a saved view can filter on them (rule 50). Split from PLMCP-77. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Reading: search_documents gets the has_duplicates filter, and the duplicates of a document, those in the trash too, can be listed with what tells them apart
- [x] #2 Writing: a group of duplicates can be resolved by keeping one and either trashing the others or merging them as versions, with a preview and confirm
- [x] #3 The preview says that merging as a version is final: the merged documents do not go to the trash and lose their metadata and notes (as merge_documents_as_versions says), while trashed ones can be restored
- [x] #4 Documents the user cannot see or may not change are stated, and nothing is changed in part without being named
- [x] #5 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [x] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the filter, duplicates in the trash, both ways of resolving and restricted users
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
1. Port TrashReader.find_in_trash (facts of trashed documents, one bounded request as read_trash), fake, adapter and contract tests.
2. search_documents has_duplicates; get_document include duplicates (DuplicateList) with what tells the copies apart.
3. Archive serial numbers in the merge text (3.5.1 hands them over), shared by both merging tools; fake follows 3.5.1 (ASN, trashed versions).
4. services/duplicate_resolution.py and tool resolve_duplicates (toolset bulk): verified in the same call, whole or refused, dry_run, dialog/confirm with a scope of documents, versions and checksums, states named after a failure.
5. decision-51, registration, snapshots, docs, coverage table; E2E (admin, READER). Guide budget: wording shortened, limit unchanged.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (decision-51). OVERVIEW_GUIDE_LIMIT 2760 to 2781 (measured 2779), the 21 bytes of resolve_duplicates, approved; nothing shortened. Definitions of the default profile: 89,362 to 93,792 bytes JSON (get_document 9,124 to 13,288, search_documents +252); the section is not in update_document, which saves 4.1 KB more; FastMCP inlines Ref, so reusing models saves nothing.
Port find_in_trash beside read_trash; the fake now hands over archive serial numbers on a merge and refuses a source with a version in the trash, as 3.5.1 does.
Deviations: notes, custom fields, page count and versions of a duplicate in the trash are null, because Paperless does not tell them; a source with a version in the trash is left to Paperless' 400; the tests of the section were written before its code but not run red.
just check: green except the macOS-only test_the_menu_driver_reports_a_failing_cli (7243 passed); diff-cover 100 %. E2E and live contract: hosted, pending (criteria 1, 2, 4, 6 wait for it).

Hosted full E2E of 4997a1c is green (run 45312691330, reported by the main agent): the live proof of criteria 1, 2, 4 and 6. Since then: a79b3be changed schema descriptions only, and PLMCP-85 was merged (24cfbd2, 14b9e41, 8fd02dc), which does not touch the code of this task except registration. OVERVIEW_GUIDE_LIMIT with both tools: 2800 (measured 2798).
<!-- SECTION:NOTES:END -->
