---
id: PLMCP-102
title: Say when a thumbnail belongs to a document in the trash
status: To Do
assignee: []
created_date: '2026-11-23T20:43:13Z'
labels:
  - tools
dependencies: []
priority: low
type: bug
ordinal: 78000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless 3.5.1 serves the thumbnail of a trashed document (views.py thumb resolves with include_deleted=True, read in the source, not probed live), while every other read answers not found. get_document_thumbnail and the thumbnail resource then show a deleted document without saying so. Permissions still apply. Found while designing PLMCP-78.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A live test shows what Paperless 3.5.1 answers for the thumbnail of a trashed document and of a trashed version
- [ ] #2 The tool and the resource either say that the document is in the trash or answer not found, as decided in the task notes, and the fake behaves like Paperless
- [ ] #3 Tool text, docs and tests cover it, including a restricted user
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
