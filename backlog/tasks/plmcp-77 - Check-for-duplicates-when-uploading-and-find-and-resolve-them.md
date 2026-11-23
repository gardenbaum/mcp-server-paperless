---
id: PLMCP-77
title: Check for duplicates when uploading, and find and resolve them
status: To Do
assignee: []
created_date: '2026-11-23T12:14:48Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 65000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Uploads by path exist (PAPERLESS_MCP_UPLOAD_DIRS) but are off by default and the model is not told how to get them; every upload adds a document even when Paperless already holds the content, and nothing checks for duplicates. Paperless 3.4 reports duplicate_documents and has_duplicates. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 upload_document and upload_document_version check before sending whether the same file exists (also in the trash and as a version), name the matches, and upload anyway only when told to; what the token user cannot see is stated
- [ ] #2 The result of a finished upload reports duplicates Paperless found
- [ ] #3 Several files or a directory can be uploaded by path in one call with one report per file
- [ ] #4 Duplicates can be listed (reading) and resolved: keep one, merge as version or trash the others, with preview and confirmation (writing)
- [ ] #5 The error for base64-only use and the tool descriptions say how to enable upload by path; the rules of decision-7 hold over HTTP
- [ ] #6 Contract and live tests against 3.4.2, including the checksum algorithm in use
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
