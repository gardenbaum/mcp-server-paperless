---
id: PLMCP-110
title: Keep the copy whose file exists when resolving duplicates
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:50:41Z'
updated_date: '2026-11-24T10:28:33Z'
labels:
  - safety
dependencies: []
priority: medium
type: bug
ordinal: 85000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
resolve_duplicates with trash can keep the document whose file Paperless cannot find and trash the copy that still has it, because checksums come from the database. Restorable until the trash is emptied, but neither the preview nor the confirmation says so. Found by the review of PLMCP-105.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A kept document whose file Paperless cannot find is refused or named in preview, confirmation and result; the same for merging as versions
- [ ] #2 Contract tests on the fake and Paperless 3.5.1, service, tool and E2E tests cover it
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
1. Failing service tests: kept file missing (trash, merge, merge_documents_as_versions), unchecked, versions, archive, acceptance.
2. One check of the root and newest file of each document (metadata/), shared by both services; require_files reads through the same helper.
3. resolve_duplicates: refuse if a copy still has the file the kept one lacks, else name it; a merge that cannot be checked is refused. merge_documents_as_versions: name it, refuse what cannot be checked.
4. Contract, tool and E2E tests (live ones hosted only), docs, decision, snapshots deliberately.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Design in decision-63. resolve_duplicates (both actions) refuses to keep a document known to lack its original while a duplicate of the call has it; every other missing file is named in preview, confirmation, result and failure. merge_documents_as_versions names it instead of refusing (the files stay as versions, and it is the way to give a document its file back). Checked are the root version (what Paperless compares) and the newest; a later version with the same checksum is the same file; an archive version alone is only named. Trash: an unchecked file is said, not refused; merges fail closed with the reason, in both tools, for the kept and the merged documents. The check runs again in the confirmed call and its findings are in the confirmation text in full.
The reading part of require_files became read_facts (5 at once, 30 s; behaviour and message of require_files unchanged).
Requests: one metadata read more per trash (two with several versions); all documents of a merge, or of a trash whose kept document lacks a file. The latter checks twice, at most 60 s.
Changed deliberately: surface snapshot (description of resolve_duplicates and merge_documents_as_versions say it, description of VersionsMerged.message: also set when a file is missing); docs/api-coverage.md regenerated (two more tools read metadata/). No limit, gate, budget or contract raised or weakened, no accepted decision edited. decision-62, which the CLI gave first, was deleted again: numbers start at 63 here.
Evidence: targeted modules, basedpyright, ruff, import contracts, diff-cover 100 %, 12 hand mutants (2 survivors got tests, all killed). The tests of the services were written together with the code and not run red before it; that they fail without it is shown by the mutants only. just check, the live contract tests and the E2E tests have not run (hosted only).

Review of d4242c4 (0 critical, 2 important, 7 minor). I1: the kept document is read once, the duplicates get what is left of the 30 s (M6: so never above 30 s). I2 (controller's ruling): resolve_duplicates does not merge a duplicate known to lack its original; merge_documents_as_versions warns when the last source lacks a file and no longer promises that its file stays; the E2E test that asserted the merge of a lost copy is corrected and downloads the kept file. M1: texts name the original, the archive version that is there, and archive-only copies. M2: refusals carry the notes. M3: versions with the checksum of the root are read too. M4: files are compared by checksum across documents, also archive versions: a kept document whose matching archive version is lost is refused (it was only named). M5: a later version of the same file counts although the root cannot be read. These replace the statements above on a second check of 60 s and on archive versions. M7: DoD 1 stays unticked: the first service, tool and fake contract tests were written with or after the code. The service tests of these fixes were seen red (19 failed) before the code; the tool and E2E tests were not. Surface snapshot: the description of resolve_duplicates names the refused merge. Nothing else of limits or gates changes.
<!-- SECTION:NOTES:END -->
