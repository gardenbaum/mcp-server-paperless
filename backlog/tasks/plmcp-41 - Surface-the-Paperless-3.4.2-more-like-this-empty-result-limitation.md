---
id: PLMCP-41
title: Surface the Paperless 3.4.2 more-like-this empty-result limitation
status: To Do
assignee: []
created_date: '2026-11-17T04:40:00Z'
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
- [ ] #1 The more_like_id tool description (src/paperless_mcp/tools/core.py) tells agents an empty result can be a Paperless index issue fixed by 'document_index reindex', with the surface snapshot change stated in the commit
- [ ] #2 The README or troubleshooting docs name the limitation and the reindex remedy
- [ ] #3 A note records when the test-side rebuild can be dropped (a pinned Paperless with the fixed Tantivy, upstream tantivy PR 2964)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
