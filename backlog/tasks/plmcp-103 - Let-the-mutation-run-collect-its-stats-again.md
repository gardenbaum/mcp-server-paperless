---
id: PLMCP-103
title: Let the mutation run collect its stats again
status: In Progress
assignee: []
created_date: '2026-11-23T23:25:36Z'
labels: []
dependencies: []
priority: high
ordinal: 79000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The weekly mutation run fails on main since PLMCP-91: mutmut renames the adapter methods (xǁClassǁname__mutmut_orig) and the observation recorder reports the renamed method, so tests/adapter/test_schema_guard.py fails while stats are collected. Found by PLMCP-76; confirmed on main by a dispatched run on 2026-11-24.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The recorder reports the method name also when mutmut renamed it, with a test
- [ ] #2 A mutation run on GitHub collects its stats, and stale equivalents of main are updated
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
