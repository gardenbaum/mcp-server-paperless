---
id: PLMCP-105
title: Read documents whose file is missing
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T07:06:14Z'
updated_date: '2026-11-24T08:46:31Z'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Failing adapter tests: metadata with null size and names; download, opened file and thumbnail that 404 on a document that exists; bulk download that 500s.
2. Domain: FileMetadata nullable (original_size, original_filename, media_filename), FileMetadata.lacks, missing_file(s) errors. Adapter: a 404 of a file is told apart with the metadata; a 500 of the bulk download likewise.
3. Fake: state.missing_files; contract (fake and live: files removed in the stack's container); service and tool tests.
4. PDF operations and e-mail ask the metadata first (a merge leaves the document out silently, an e-mail failure is reported as unknown delivery).
5. E2E, surface snapshot, docs. Nothing live runs locally.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented; red before green at adapter, service, tool and (fake) contract level. The service tests of PDF and e-mail were red against an empty require_files; the E2E and live contract tests were written with the code and have not run (hosted only).
Facts from the 3.5.1 source: metadata/ has size null when the path is no file; filename, original_filename and archive_filename are nullable in the database, checksum and MIME type are not; download/ and thumb/ turn FileNotFoundError into a 404; bulk_download/ does not catch it (500); e-mail answers 500; merge logs it and leaves the document out; rotate, edit_pdf, remove_password and reprocess fail in their task. E-mail attaches the root's own file, the others the newest version's.
Design: a 404 of a file or thumbnail asks the metadata, so a missing file is no longer 'No document N exists'; the error names the other file if it is there. A 500 of bulk_download looks at the metadata of its documents (at most 30 s, only then) and names them; anything else stays the failure it was. PDF operations and email_documents ask the metadata first, before the confirmation: one more request per document (at most 20 and 50). get_document_pages does not switch to the archive version on its own; the hint names it. Parts of a file without a size open it first, so the missing file is named.
Surface snapshot: file_metadata.original_size, original_filename and media_filename are nullable; the descriptions of original_size and archive_size say what null means (the latter is longer, nothing was cut). docs/api-coverage.md: more tools read metadata/. No limit, gate, contract or decision is changed; no tool added. Existing contract test adjusted for the nullable types (the size must still be non-zero, the name must still end in the title). Fake: a listed version without a file entry of its own answers the document's file instead of 'no version' (the e-mail scope test lists such a root).
Live: tests/e2e/helpers.lose_file removes a file in the container or nulls the names with manage.py shell; used by the contract tests and one E2E test in test_downloads.py.
Not changed: share links and bundles (creating one needs no file; a failed bundle build is reported by its last_error).
<!-- SECTION:NOTES:END -->
