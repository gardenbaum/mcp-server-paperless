---
id: PLMCP-116
title: Cache the built gitleaks hook in CI
status: Done
assignee: []
created_date: '2026-11-24T21:01:12Z'
updated_date: '2026-11-24T21:01:14Z'
labels: []
dependencies: []
ordinal: 98000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
just secrets rebuilds gitleaks in every CI job and failed three times on 2026-11-24 because proxy.golang.org or sum.golang.org broke the download. Owner approval of 2026-11-24.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The check and audit jobs restore the built hook from a cache; the whole-history scan still runs in every job
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
Key: OS, Python and the hash of .pre-commit-config.yaml, so a new gitleaks version is built anew. The scan is unchanged.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
actions/cache for the pre-commit environments in check and audit.
<!-- SECTION:FINAL_SUMMARY:END -->
