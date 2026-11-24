---
id: PLMCP-86
title: Find and resolve duplicate documents
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:21:59Z'
updated_date: '2026-11-24T02:12:49Z'
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
- [ ] #1 Reading: search_documents gets the has_duplicates filter, and the duplicates of a document, those in the trash too, can be listed with what tells them apart
- [ ] #2 Writing: a group of duplicates can be resolved by keeping one and either trashing the others or merging them as versions, with a preview and confirm
- [ ] #3 The preview says that merging as a version is final: the merged documents do not go to the trash and lose their metadata and notes (as merge_documents_as_versions says), while trashed ones can be restored
- [ ] #4 Documents the user cannot see or may not change are stated, and nothing is changed in part without being named
- [ ] #5 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [ ] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover the filter, duplicates in the trash, both ways of resolving and restricted users
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
