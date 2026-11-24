---
id: PLMCP-105
title: Read documents whose file is missing
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T07:06:14Z'
updated_date: '2026-11-24T10:08:29Z'
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
- [x] #1 Metadata with null size and names is read; what needs the file says that Paperless cannot find it
- [x] #2 Adapter, contract (fake and 3.5.1 if it can be produced) and tool tests
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

Review of db68e91 (0 critical, 2 important, 8 minor) fixed except M5, which becomes a task of its own. I1: a null name is not a missing file (3.5.1 falls back to {pk:07}{type}, the default name); only a null size is. This corrects the fact above that a nameless document is missing its file. I2 (controller's decision): the check before PDF operations and e-mails reads at most 5 documents at once and takes at most 30 s for all; a document that could not be checked passes (the operation then fails by itself as before), only a seen null size refuses. Merges fail closed with 'could not be checked', because Paperless leaves such a document out silently; so a merge can now be refused when metadata/ fails or is slow. M1: a 404 of a file the metadata lists is a neutral error. M2: refusals count the selection and name the other kind if it works for all. M3: the lookup after a failed bulk download goes on past a document that cannot be read and says the list may be incomplete. M4: the open of a part of unknown size is inside the time limit; no timed test, the 30 minutes are not injectable. The text 'does not know the size and checksum' became 'does not give ...' (test adjusted). M6 to M8 done.

Delta review: a merge that cannot be checked says why (the first error, or that the check took too long) and offers to leave the documents out; tests for the incomplete check, the hidden original in the pages hint and the order of the ids (two hand mutants killed).

Merged with #124 (9266626). Review and delta review clean, Codex without findings. Hosted full E2E 45360832807 green on 129ff56, with a file removed in the container and nulled names. DoD 1 stays open (contract, tool and E2E tests of the first commit were written with the code), DoD 2 unchecked (macOS-only failures). Follow-up: PLMCP-110.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Documents whose file Paperless cannot find are readable; downloads, pages, thumbnails, bulk downloads, PDF operations and e-mails name the missing file; a merge is refused when a file is missing or cannot be checked.
<!-- SECTION:FINAL_SUMMARY:END -->
