---
id: PLMCP-114
title: Keep other documents out of the more-like-this test of a thousand matches
status: In Progress
assignee: []
created_date: '2026-11-24T16:46:43Z'
updated_date: '2026-11-24T17:31:29Z'
labels: []
dependencies: []
ordinal: 97000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
test_a_thousand_matches_are_read_well_within_the_timeout[similar] failed on main 8f940a8 (full E2E 45436547279): the bare documents shared the words 'of a test' with documents of other modules in the shard, so more-like-this found 1005, five of them foreign, and the selection rightly stopped at 1001.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The similar case asserts only what Paperless and the selection guarantee, and the full hosted E2E is green
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
The first attempt (words of its own for the bare documents) did not help: full E2E 45441107004 still found 1005. Paperless 3.5.1 builds more-like-this without a list of fields (documents/search/_backend.py), and the green runs found 1000 although the document itself is left out, so a foreign document matched there too: 'at most 1,000' was never guaranteed. The similar case now asserts at least 999 matches and that 1,001 at most are read; the cases title and text keep exactly 1,000, the time bound is unchanged. Which field the five foreign documents share was not determined.
<!-- SECTION:NOTES:END -->
