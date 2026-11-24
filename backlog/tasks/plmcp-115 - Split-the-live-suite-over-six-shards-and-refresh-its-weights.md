---
id: PLMCP-115
title: Split the live suite over six shards and refresh its weights
status: Done
assignee: []
created_date: '2026-11-24T20:21:56Z'
updated_date: '2026-11-24T20:59:52Z'
labels: []
dependencies: []
ordinal: 97000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The live suite has grown from about 2970 s to about 4300 s of tests; shard 1 of 4 ran into the 25-minute hang guard on 957046b and on main ff22aa3 although no test failed. Decision-58 says the shard count or the weights change then, not the guard.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Six shards and up to six parts of the pull-request tier, weights from hosted runs, even within 15 percent, no test dropped
- [x] #2 Guards, tier budget and worker count are unchanged
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
Full hosted E2E 45480435671 with six shards green, 10 to 16 minutes each. Guards, budget and workers unchanged. decision-76 accepted by the owner on 2026-11-24.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Six shards and up to six PR parts; weights from four hosted runs of 2026-11-24.
<!-- SECTION:FINAL_SUMMARY:END -->
