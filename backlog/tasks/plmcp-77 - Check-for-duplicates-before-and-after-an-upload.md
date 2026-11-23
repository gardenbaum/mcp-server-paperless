---
id: PLMCP-77
title: Check for duplicates before and after an upload
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:48Z'
updated_date: '2026-11-23T23:23:39Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 64000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
By default Paperless keeps duplicates (with PAPERLESS_CONSUMER_DELETE_DUPLICATES=true it rejects them and the task fails); the upload tools do not check. A document reports duplicate_documents, and the list filter has_duplicates (today only in saved views) finds them. Uploads by path exist over stdio (PAPERLESS_MCP_UPLOAD_DIRS) but are off by default and the model is not told how to get them. Uploading several files or a directory, and finding and resolving duplicates, are tasks of their own. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 upload_document and upload_document_version check before sending whether a visible document has the same original checksum, name the matches and upload anyway only when told to
- [ ] #2 Versions and the trash are checked before sending if a probe of 3.5.1 shows a way; otherwise they are reported after the upload from duplicate_documents; the tool says what was not checked (trash, versions, archive checksum, documents the user cannot see)
- [ ] #3 The result of a finished upload reports the duplicates Paperless found, also through get_task when the upload was not waited for, and a rejected duplicate names the existing document
- [ ] #4 Over stdio the error for base64-only use and the tool descriptions say how to enable upload by path; over HTTP nothing suggests it, and the rules of decision-7 hold
- [ ] #5 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [ ] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover matches, no match, upload anyway, rejection by Paperless, a restricted user and the checksum algorithm in use (SHA-256 is pinned for versions)
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
Approved design (owner, 2026-11-23), part 77. Failing test first at each level.
1. Fake: real checksums, duplicates, root, version rows, rejection (tests/fakes/duplicates.py); adapter and contract tests.
2. Ports/adapters: DocumentQuery.checksum, get_root, get_duplicates, list_versions, TrashedDocument.checksum, TaskStatus.duplicate_of; a rejected task names no document (get_task, list_tasks).
3. services/duplicates.py DuplicateFinder (bounded scan: 5,000 documents, 500 trash entries, 20 trashed versions); before/after/rejected; TaskService(duplicates=); uploads and versions with confirm.
4. Path hints: third stdio form, middleware, errors.
5. Decision 47, docs, snapshots, E2E, own stack for rejection (e2e-duplicates). No local E2E.
<!-- SECTION:PLAN:END -->
