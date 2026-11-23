---
id: PLMCP-75
title: Correct the text of a document
status: To Do
assignee: []
created_date: '2026-11-23T12:14:46Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 63000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Once a page can be read as an image, a wrong or missing OCR passage should be correctable, so search and the AI find it. get_document_content only reads; check whether update_document or the API takes content. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The content of a document, and of a version if Paperless allows it, can be replaced as a whole or by an exact passage, with a preview of the change and a confirmation
- [ ] #2 A passage that is missing or not unique changes nothing and says so
- [ ] #3 The tool says that reprocessing overwrites the correction, and full-text search finds the new text (live test)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
