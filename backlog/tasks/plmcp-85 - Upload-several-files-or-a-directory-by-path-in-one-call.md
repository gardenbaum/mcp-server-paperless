---
id: PLMCP-85
title: Upload several files or a directory by path in one call
status: To Do
assignee: []
created_date: '2026-11-23T12:21:58Z'
updated_date: '2026-11-23T18:30:57Z'
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
