---
id: PLMCP-41
title: Surface the Paperless 3.4.2 more-like-this empty-result limitation
status: Done
assignee:
  - '@claude'
created_date: '2026-11-17T04:40:00Z'
updated_date: '2026-11-17T04:48:00Z'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 41000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
On Paperless 3.4.2 (Tantivy 0.26) a more-like-this query panics in BM25 scoring once soft-deleted index entries make a term's document frequency exceed the live document count; Paperless catches the panic, logs 'More Like This scoring panicked' and answers an empty list with HTTP 200 (documents/search/_backend.py more_like_this_ids). Any instance where documents were edited or deleted can hit it, so search_documents(more_like_id=...) may wrongly report no similar documents. PLMCP-40 made the live contract test rebuild the index first (tests/e2e/helpers.py rebuild_search_index), which hides the limitation from tests. Found by the PLMCP-40 review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The more_like_id tool description (src/paperless_mcp/tools/core.py) tells agents an empty result can be a Paperless index issue fixed by 'document_index reindex', with the surface snapshot change stated in the commit
- [x] #2 The README or troubleshooting docs name the limitation and the reindex remedy
- [x] #3 A note records when the test-side rebuild can be dropped (a pinned Paperless with the fixed Tantivy, upstream tantivy PR 2964)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red test: surface test asserts more_like_id description mentions index/reindex hint.
2. Add short hint to more_like_id Field description in tools/core.py; regenerate surface snapshot.
3. README known-limitations note with 'document_index reindex' remedy.
4. Note in tests/e2e/helpers.py rebuild_search_index and task notes: drop rebuild once pinned Paperless ships Tantivy with upstream tantivy PR 2964.
5. just check, commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
more_like_id description now ends with a short hint (empty result on Paperless 3.4.2 can be a search-index fault; fix: document_index reindex). Surface snapshot tests/tools/__snapshots__/test_surface.ambr regenerated (3 snapshots updated, only that description text). No description-length test exists; the budget test counts tools. README gained a Known limitations section. Test-side rebuild (tests/e2e/helpers.py rebuild_search_index) can be dropped, with its callers, once the pinned Paperless ships a Tantivy with the fix (upstream tantivy PR 2964); then also remove the description hint and README note. Red: test_more_like_id_warns_... failed before the change; just check green.

Controller: docs and description only, no logic; reviewed by the controller instead of a separate reviewer agent.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a short index-fault hint (document_index reindex) to the more_like_id parameter description, a README Known limitations section, and a note in tests/e2e/helpers.py rebuild_search_index on when the rebuild can go (Tantivy fix, upstream PR 2964). Surface snapshot regenerated (3 description lines). Test written first, red then green; just check passes.
<!-- SECTION:FINAL_SUMMARY:END -->
