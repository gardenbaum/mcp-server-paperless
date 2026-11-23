---
id: PLMCP-75
title: Correct the text of a document
status: To Do
assignee: []
created_date: '2026-11-23T12:14:46Z'
updated_date: '2026-11-23T12:22:01Z'
labels: []
dependencies:
  - PLMCP-74
priority: high
type: feature
ordinal: 66000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Once a page can be read as an image, a wrong or missing OCR passage should be correctable, so search and the AI find it. The API takes content in PATCH /api/documents/{id}/ (per version with ?version=); no tool exposes it, and get_document_content reads the latest version only. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The text of any version can be read
- [ ] #2 The content of a document or of one version can be replaced as a whole or by an exact passage, with a preview of the change and a confirmation
- [ ] #3 A passage that is missing or not unique changes nothing and says so; if the text changed since the preview, nothing is written
- [ ] #4 A decision supplements decision-12: content changes need confirm
- [ ] #5 The tool says that reprocessing overwrites the correction, and full-text search finds the new text (live test)
- [ ] #6 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [ ] #7 Contract tests on the fake and Paperless 3.4.2, tool tests and E2E cover whole and passage replacement, a version, a missing or ambiguous passage, a change in between and a restricted user
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
