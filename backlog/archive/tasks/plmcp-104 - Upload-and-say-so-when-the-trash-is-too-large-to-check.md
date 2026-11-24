---
id: PLMCP-104
title: Upload and say so when the trash is too large to check
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T07:06:13Z'
updated_date: '2026-11-24T08:24:01Z'
labels: []
dependencies: []
priority: high
ordinal: 79000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Above 64 MiB of trash the duplicate check fails and nothing can be uploaded (decision-47). The owner decided on 2026-11-24 ('bitte wie von dir empfohlen'): upload, and say that the trash was not checked, as above 500 entries. Outages, timeouts and every other failure still refuse.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An answer over the byte limit is a named gap in not_checked for upload_document, upload_document_version and upload_documents; nothing else of the rule changes
- [x] #2 A new decision supersedes exactly that part of decision-47
- [x] #3 Service, adapter, contract and tool tests cover the limit crossed, and every other failure still refuses
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red tests (service, batch, adapter, contract, tool). 2. DuplicateFinder: AnswerTooLarge on reading the entries becomes a gap (TRASH_UNREAD) instead of TrashTooLargeError; the trash is then counted in a small request, so the recount at the end still notices changes; if even one entry is too large there is no count. 3. The gap sentence goes into not_checked and into the confirmation text. 4. rejected() unchanged: only a title is missing. 5. decision-52, docs/configuration.md.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red before the code at service, batch, adapter, contract (fake) and tool level (18 tests), then green. decision-52 (51 belongs to PLMCP-86). The old service test that a trash over the limit is a failed check is replaced by the gap tests (the part decision-52 supersedes). No snapshot changes: tool descriptions are unchanged (they already point to duplicates.not_checked). No E2E or live run locally.

Full local run: 7371 passed; diff-cover 100 % (18 lines). Not run: the live side of the contract test (CI).

Review of 6745069: m1 to m3 added to decision-52 as consequences; m4: the sentence says that the first 500 entries are meant.

Review clean on 546650c, one wording fixed in 81e53b7; merged with #120 (9f79fd4). Codex without findings; hosted full E2E 45349964929 green on acff734, whose tree equals the merged one. DoD 2 stays unchecked (the macOS-only failure).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
An upload is no longer refused because the trash is larger than 64 MiB: the duplicate check names the gap in not_checked and in the confirmation (decision-52 supersedes that part of decision-47). Every other failure still refuses.
<!-- SECTION:FINAL_SUMMARY:END -->
