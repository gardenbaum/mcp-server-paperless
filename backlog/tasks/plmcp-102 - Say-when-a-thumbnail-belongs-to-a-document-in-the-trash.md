---
id: PLMCP-102
title: Say when a thumbnail belongs to a document in the trash
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T20:43:13Z'
updated_date: '2026-11-24T21:23:58Z'
labels:
  - tools
dependencies: []
priority: low
type: bug
ordinal: 78000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless 3.5.1 serves the thumbnail of a trashed document (views.py thumb resolves with include_deleted=True, read in the source, not probed live); by the source also its preview and its file download. Every other read answers not found. get_document_thumbnail, the thumbnail resource and possibly the downloads, parts, page images and the raw toolset then show a deleted document without saying so. Permissions still apply. Found while designing PLMCP-78.
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Contract tests: thumbnail of a trashed document, of a trashed version, with a view grant, without (fake and live).
2. Red: service, tool and resource tests that get_document_thumbnail refuses what is in the trash and says so, also for a restricted user with a view grant.
3. Green: FileService.thumbnail looks the document up among the live ones after the image came (one light list request; the metadata only for an id that is not listed, such as a version's).
4. Tool text, docs, snapshot, E2E, decision (proposed: the owner decides).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Decision of the implementer, for the owner to confirm (decision-78, proposed): answer an error that says the id is in the trash, show no image. Recommended, because the alternative (image with a note) keeps showing what an owner deleted to users with a view grant, and the resource has no report for the note. Only the thumbnail was affected: downloads, parts, page images, contact sheets and PDF tools read the document or its metadata first (404 in the trash), bulk_download takes live documents only (v3.5.1 source), the raw toolset refuses file endpoints. The fake already served trashed thumbnails as Paperless does. Surface snapshot: one sentence in the description of get_document_thumbnail. docs/api-coverage.md regenerated (one count). Tests red first at tool level (tool and resource, admin and reader); live proof by the new contract and E2E tests on GitHub.
<!-- SECTION:NOTES:END -->
