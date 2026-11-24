---
id: PLMCP-105
title: Read documents whose file is missing
status: To Do
assignee: []
created_date: '2026-11-24T07:06:14Z'
labels: []
dependencies: []
priority: medium
ordinal: 80000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless answers metadata with size null when the file of a document or version is missing; our model requires a number, so get_document with file metadata, download_document and get_document_pages refuse the document with 'unexpected response'. PLMCP-77 fixed it for checksums only. Found by PLMCP-77.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Metadata with null size and names is read; what needs the file says that Paperless cannot find it
- [ ] #2 Adapter, contract (fake and 3.5.1 if it can be produced) and tool tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
