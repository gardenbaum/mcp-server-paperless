---
id: PLMCP-113
title: Keep tests about hidden documents independent of random task ids
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:53:15Z'
updated_date: '2026-11-24T12:53:54Z'
labels:
  - tests
dependencies: []
priority: medium
type: bug
ordinal: 90000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
check (3.15) of #131 (job 138434317468) failed in test_a_rejection_for_a_hidden_document_never_names_it: the test looks for the document id 4711 as text in the whole JSON answer, and the random task id of that run was 9db47116-2a6c-498a-8a1e-899e77ab679d. The same check stands in test_batch_uploads.py. A red required check that has nothing to do with the change costs a full CI run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Neither test can fail because of a random task id or a timestamp
- [ ] #2 Both tests still fail when the hidden document id is in the answer
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
Use an id of 13 digits: a group of a uuid has at most 12 hex digits, a timestamp has at most 6 decimals.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence: job 138434317468 printed task_id 9db47116-2a6c-498a-8a1e-899e77ab679d (contains 4711). Making the document visible (owner=None) makes the first test fail and prints 4711000000000 in one piece; the second test searches the same form. Other 'not in' checks on short numbers look at messages without random ids.
<!-- SECTION:NOTES:END -->
