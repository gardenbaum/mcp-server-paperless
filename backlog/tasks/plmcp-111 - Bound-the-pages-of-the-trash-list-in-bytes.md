---
id: PLMCP-111
title: Bound the pages of the trash list in bytes
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T09:06:02Z'
updated_date: '2026-11-24T22:34:31Z'
labels:
  - safety
dependencies: []
priority: low
type: bug
ordinal: 90000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
list_trash pages (100 rows with full texts) have no byte limit and accept compressed answers; restore, empty by ids, get_trashed_file and the ids filter fall back to the same request. A plain bound would stop restore by ids behind a compressing proxy. Found by PLMCP-78.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every read of whole trash rows is bounded, also when the answer is compressed, and restore by ids still works behind a compressing proxy
- [x] #2 Adapter, service and contract tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: adapter tests of list_trash (byte limit while reading, also decoded from gzip, deflate and zstd; small bomb stops early; unknown encodings refused; broken body), service tests (pages that are too large are read in smaller ones for ids; an entry too large alone; list and all=true), contract test of the limit.
2. Green: list_trash takes max_bytes and reads through read_json, which can decode compressed bodies in bounded pieces (httpx2 decodes at most 1 MiB at a time since 2.16.0, our minimum). The other trash reads stay: uncompressed only (decision-54).
3. TrashScan.locate and empty all=true fall back to pages of 10 and 1 entries where 100 (1,001) are over the limit; a decision records it.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
decision-77. list_trash takes max_bytes (64 MiB) and reads through read_json, which can now unpack gzip, deflate and zstd in pieces of at most 1 MiB (httpx2 2.16.0, our minimum). Ids are looked for in pages of 100, 10 and 1 entries where larger ones are over the limit; an entry over it alone is passed, and a missing id is then an error of its own. empty all=true falls back to pages only if they hold exactly the counted entries. The other reads stay as decision-54 has them. The port gains max_bytes, so the fake and the tests that override it changed; the fake bounds list_trash in one place. Code and tests were written together; red was shown by mutation (no limit: 11 tests; compressed refused: 18; pages never larger again: 1). Trade-off for the owner: filters and the duplicate check of uploads could work behind a compressing proxy in the same way (changes decisions 47 and 54).

Review: hostile compressed answers. The body is now unpacked with zlib in paperless/unpacking.py: raw bytes count, data after the end is refused at once, gzip and deflate only and asked for explicitly, error bodies bounded too; tests red first (2.7 GB of process memory before; under 40 MB now). zstd is not taken any more (decision-77, proposed). The pages search has a limit of 1,000 requests; an unread entry is told beside found entries and reasons; the hint that pages can skip entries stands for restore too. Survivor tests added after the code, shown by mutation: while, scan.complete, wanted <= found (both), aclosing, smaller pages, request limit. Not caught by a test: the loop that drains what still waits inside zlib once all input has gone in; no input was found that reaches it. Not changed: other streamed reads and the error bodies of downloads still use httpx2's decoders and keep data after the end.

Second review: error bodies of bounded reads are cut to the first 64 KiB that arrive and decoded by httpx2 again (zstd details kept, cut not emptied); the own test of an endless error body now allows these 64 KiB (67 pieces, before 2). all=true names the request limit; STOPPED no longer claims the trash is longer. Tests for the end-of-stream check, the zlib header check and the wording, each failing under mutation. Follow-ups: PLMCP-117, PLMCP-118.

decision-77 accepted by the owner on 2026-11-24. DoD 1 stays open: not fully written test first (named in the PR). DoD 2 and 4: CI and merge of #151.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Trash list reads are bounded on the unpacked and the raw size; restore by ids works behind a compressing proxy (decision-77).
<!-- SECTION:FINAL_SUMMARY:END -->
