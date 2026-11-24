---
id: PLMCP-85
title: Upload several files or a directory by path in one call
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:21:58Z'
updated_date: '2026-11-24T01:40:06Z'
labels: []
dependencies:
  - PLMCP-77
priority: high
type: feature
ordinal: 64100
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Uploads by path take one file per call, so a folder of scans needs one call per document. Split from PLMCP-77. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Over stdio, several files or a directory inside PAPERLESS_MCP_UPLOAD_DIRS can be uploaded by path in one call with one report per file (uploaded, duplicate, refused, failed)
- [ ] #2 The number of files and the total bytes are limited and stated; what does not fit is named with how to continue
- [ ] #3 Walking a directory applies the rules of single files: symlinks must end inside the allowed directories, hard-linked and dot files are never read, no traversal; HTTP never offers it (decision-7)
- [ ] #4 Every file gets the duplicate check of PLMCP-77, and one failing file does not stop the others or hide their result
- [ ] #5 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [ ] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover mixed outcomes, limits, symlinks, hard links, dot files, traversal and races
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
1. domain/uploads: UploadBatch model. 2. services/upload_source: list_allowed (walk on descriptors), tests first. 3. services/duplicates: open(checksums) -> Batch with find(): originals of every file looked up before the one snapshot and again per file, 25 s limits, a moved trash re-reads everything once. 4. services/batch_uploads: two passes (read+hash, then check+send), limits 25 files / 100 MiB / 40 s, follow bounded. 5. tools/bulk_stdio: upload_documents, only over stdio with upload directories; local tables beside TOOL_TOOLSETS/ANNOTATIONS, so nothing names it over HTTP. 6. decision-50 supersedes decision-7. 7. contract, tool, E2E tests, docs, snapshots. ENABLE wording shortened to stay inside OVERVIEW_GUIDE_LIMIT.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-77: DuplicateFinder.snapshot() and before() alone lack the double lookup of originals (before and after the versions) that cleared() does, so a document merged into another one between them is missed; they also have no time limit and no second attempt. A batch that reuses one snapshot needs its own answer to both.

Owner approvals 2026-11-23 (design-plmcp-77-85-86): file names of allowed upload directories may become visible to the model over stdio, never HTTP (new decision supersedes decision-7); 25 files, 100 MiB, 40 s per call; recursive off by default; duplicates are confirmed one by one; tool in toolset bulk, its budget rises; default profile stays 20 of 20.
<!-- SECTION:NOTES:END -->
