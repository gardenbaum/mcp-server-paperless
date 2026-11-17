---
id: PLMCP-40
title: Make the live more_like contract test reliable
status: Done
assignee:
  - '@claude'
created_date: '2026-11-13T16:56:00Z'
updated_date: '2026-11-17T04:40:00Z'
labels: []
milestone: m-3
dependencies: []
type: bug
ordinal: 40000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
tests/contract/test_document_reader.py::test_more_like_finds_other_documents[live] returned no hits at all on a fresh CI stack (gardenbaum/paperless-mcp PR #32, e2e run of d2de355; the rerun passed) and fails on the shared local stack, whose Whoosh index is stale. The PR did not touch the index, the seed or this test. Likely cause: the index entry of the seeded private document is not there or not committed yet when the test runs, for example because the contract bulk tests just before it trigger asynchronous index updates. Find the root cause on Paperless 3.4.2 (documents/index.py more_like, the index update tasks) and make the test wait for what it really depends on, or fix the seeding. Do not skip, quarantine or retry the test blindly.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The root cause of the empty more_like result is found and written in the task notes
- [x] #2 The test waits for or establishes the index state it depends on, without a blind retry
- [x] #3 The test passes on a fresh stack and on a stack after the full contract suite (bulk tests included)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce on the shared stack (done: empty result after the bulk tests). 2. Find the cause in Paperless 3.4.2 documents/search/_backend.py. 3. Add Backend.rebuild_search_index (live: wait for index-writing tasks, then document_index reindex) and call it in the more_like test.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause (AC 1): Paperless 3.4.2 indexes with Tantivy 0.26.2, not Whoosh. Every update or deletion of a document (add_or_update removes then re-adds; bulk edits run bulk_update_documents, which does the same) leaves the old entry as a soft-deleted document in its segment until a merge. A term's document frequency counts those entries. more_like_this_ids (documents/search/_backend.py) builds a Tantivy more_like_this query; when a query term's doc_freq exceeds the live document count the BM25 idf code panics (bm25.rs:53, upstream tantivy PR 2964), Paperless catches the BaseException, logs 'More Like This scoring panicked' and returns [] with HTTP 200. So the seeded private document is indexed and committed, and the permission filter is not involved: the query itself dies. The seed PATCHes the two documents several times and the contract bulk tests (which run before test_document_reader) update and delete many more, so the condition is reached on a fresh CI stack too, depending on merge timing; a long-lived stack always has it. Reproduced here: after 'document_index reindex' the test passes, after test_bulk it fails again (live log shows the warning each time). Bulk edits also write the index asynchronously (celery bulk_update_documents), so a rebuild must wait for those tasks first.

Evidence: red before the fix (bulk tests, then the more_like test: empty set, same on the full suite run 8 min). Green after: same sequence 3 passed; full 'pytest -m e2e tests/contract --no-cov' under flock on the long-lived stack: 490 passed, 5 skipped (AI off), more_like tests included; fresh stack (just e2e-down, e2e-up, restored and seeded afterwards): test_tags_are_added... + whole test_document_reader.py 46 passed. just check passes. Known side finding: /api/token/ is throttled, so back-to-back runs can fail in fixtures with 429 for a minute. Not changed. The rebuild shells out to docker (finds the container that publishes the e2e port); it is a test-only helper.

Review fixes: added check_workflows to the awaited index writers, timeouts on both docker calls (30 s, 300 s reindex), clear docker ps error with stderr. The deferred index_document/remove_document_from_index fallbacks are untracked by Paperless and cannot be polled; accepted as a low-probability residual. The hidden product limitation (empty more_like results on 3.4.2) is tracked in PLMCP-41.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The live more_like contract test returned nothing because Tantivy 0.26.2 panics in the more_like_this query when soft-deleted index entries (left by every document update or deletion, e.g. the bulk tests) push a term's document frequency above the live document count; Paperless catches the panic and answers an empty list. Fix: Backend.rebuild_search_index (live: wait for the recent index-writing tasks, then document_index reindex in the e2e container; fake: no-op) is called by the test before the search. No retry, no skip, assertions unchanged. Verified on the long-lived stack after the full contract suite (490 passed) and on a fresh stack.
<!-- SECTION:FINAL_SUMMARY:END -->
