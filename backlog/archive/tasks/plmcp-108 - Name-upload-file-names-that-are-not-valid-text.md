---
id: PLMCP-108
title: Name upload file names that are not valid text
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T07:06:17Z'
updated_date: '2026-11-24T22:42:47Z'
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
- [x] #1 Both tools refuse such a name with a reason; the download sink keeps its behaviour
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: service and tool tests of upload_document and upload_document_version with a surrogate in the file name (content, given name with a path, the path's own name).
2. Green: move upload_name of batch_uploads (PLMCP-85) next to clean_filename and use it in both services; clean_filename and the download sink stay as they are.
3. Real file with bytes that are no text where the file system allows it (Linux CI).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
upload_name of PLMCP-85 moved from batch_uploads to allowed_paths; UploadService and VersionUploadService use it (content, given name with a path, the path's own name), with the same refusal as upload_documents. clean_filename is unchanged, so the download sink keeps its own message (its tests are unchanged). Over HTTP (required filename) the schema already refuses such a name with a reason; the internal error came from the stdio variants (optional filename). Red before the change: 4 service tests and both stdio variants of the tools. The case of a file whose own name is no text runs on Linux only (macOS refuses such names), so in CI. No surface change, no decision.

Extended on review: a title (upload_document) and a version label (upload_document_version, set_document_version_label, merge_documents_as_versions) with lone surrogates are refused with a reason, too; tool tests red first, over both variants for uploads. upload_documents has no title. Names and custom field values were not looked at.

Merged in #150; also title and version label. Other text fields: PLMCP-118.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Upload names, titles and version labels that are not valid text are refused with a reason.
<!-- SECTION:FINAL_SUMMARY:END -->
