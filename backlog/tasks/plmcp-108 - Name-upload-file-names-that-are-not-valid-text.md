---
id: PLMCP-108
title: Name upload file names that are not valid text
status: To Do
assignee: []
created_date: '2026-11-24T07:06:17Z'
labels: []
dependencies: []
priority: low
ordinal: 83000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
upload_document and upload_document_version end with a bare UnicodeEncodeError (masked as an internal error) for a file name with surrogates; upload_documents refuses it with a reason (PLMCP-85).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Both tools refuse such a name with a reason; the download sink keeps its behaviour
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
