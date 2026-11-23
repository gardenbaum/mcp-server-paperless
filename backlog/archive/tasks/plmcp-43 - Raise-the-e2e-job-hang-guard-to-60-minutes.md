---
id: PLMCP-43
title: Raise the e2e job hang guard to 60 minutes
status: Done
assignee: []
created_date: '2026-11-17T07:24:00Z'
updated_date: '2026-11-17T07:24:00Z'
labels: []
dependencies: []
priority: high
type: chore
ordinal: 43000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The post-merge e2e job on main (CI run 44031694561, commit 6c1ab58) was cancelled by its 35-minute timeout at 82% of the suite; the run before took 33:54 in pytest alone. With every toolset merged the suite now takes about 40 minutes on a hosted runner. The timeout only guards against hangs and is no quality gate (see the comment in .github/workflows/ci.yml); PLMCP-39 is the real fix and lowers it again.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The e2e job timeout is 60 minutes with the reason in the workflow comment
- [ ] #2 The next post-merge e2e run on main finishes within the limit
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Raised timeout-minutes of the e2e job from 35 to 60 and updated the comment. Not a gate change: the limit only catches hangs. AC #2 is verified by the first post-merge run on main; PLMCP-39 remains the fix for the suite's length.
<!-- SECTION:FINAL_SUMMARY:END -->
