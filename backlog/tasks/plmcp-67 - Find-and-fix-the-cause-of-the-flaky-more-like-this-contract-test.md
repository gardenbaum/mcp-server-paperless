---
id: PLMCP-67
title: Find and fix the cause of the flaky more-like-this contract test
status: To Do
assignee: []
created_date: '2026-11-23T11:08:14Z'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 61000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
test_more_like_finds_other_documents[live] failed twice in the full E2E suite on 2026-11-23 (runs 45095262481 at fc19694 and 45157307609 at 5a8849a, both shard 2/3): the search answered no documents, although the helper waits for index-writing tasks and rebuilds the index first. Suspected, not proven: Tantivy's BM25 panic in Paperless 3.4.2 (see rebuild_search_index in tests/e2e/helpers.py), triggered by an index write that ends after the rebuild. The job log holds no Paperless log, so the cause is not visible. A red suite on main blocks a release. Owner approved on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The cause is proven with Paperless log evidence, or the failing job captures that log so the next failure proves it
- [ ] #2 The test no longer fails from that cause, without being skipped, retried blindly or weakened
- [ ] #3 The test still fails when more-like-this really finds nothing
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
