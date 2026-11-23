---
id: PLMCP-74
title: Show the pages of a document as readable images
status: To Do
assignee: []
created_date: '2026-11-23T12:14:45Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 62000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
get_document_thumbnail shows only a small image of page 1, and download_document attaches at most 512 KiB by default, while scans are often over 1 MB. An unreadable OCR spot (a signature date) could not be checked. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A tool returns any page or page range as images a model can read, from the original, the archive file and any version
- [ ] #2 Resolution is selectable and a region of a page can be enlarged, so small print is readable
- [ ] #3 It works for PDFs and for image originals (JPEG, PNG, TIFF, WebP), for encrypted or broken files it says why not
- [ ] #4 Large documents stay within stated byte, page, memory and time limits without changing the server's configuration; pages that do not fit are named with how to get them
- [ ] #5 The renderer is chosen in a decision (licence, size, security of parsing untrusted files, arm64, image scan) and runs within the container's restrictions
- [ ] #6 Permissions are those of the token user; stdio and HTTP both work
- [ ] #7 Contract, adapter, tool and live tests; docs, tool reference and the default-profile budget are settled deliberately
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
