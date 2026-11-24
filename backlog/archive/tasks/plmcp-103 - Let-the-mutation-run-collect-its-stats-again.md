---
id: PLMCP-103
title: Let the mutation run collect its stats again
status: Done
assignee: []
created_date: '2026-11-23T23:25:36Z'
updated_date: '2026-11-24T07:06:11Z'
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
- [x] #1 The recorder reports the method name also when mutmut renamed it, with a test
- [x] #2 A mutation run on GitHub collects its stats, and stale equivalents of main are updated
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Also fixed here: the proxy of tests/e2e/pages.py (PLMCP-74) left its connection to Paperless open when a client dropped a slow answer; the ResourceWarning failed an unrelated test (tests/e2e/test_search.py) in the hosted full E2E of PLMCP-76 (run 45293962464). Reproduced against a local stand-in before the fix, gone after it.

The run also outgrew its 60 minutes (28 minutes on 2026-11-22): since PLMCP-74 the renderer's image tests ran in every full pass of mutmut although they test none of the mutated code. They are left out (this can only lower the score) and the time limit is 150 minutes.

Hosted run 45296581735 on f6ef4c2 (main 7fde945 with this branch) collected its stats and scored 99.2 % (18329 killed, 20 timeouts, 157 survived of 18742, 236 reviewed equivalents) in 97 minutes. It failed only on 11 stale equivalents, which are removed here: that can only lower the score. PLMCP-77 and the follow-up of PLMCP-76 came after that run, so the next one may name new stale entries.

Independent review of 302dea9: nothing blocking; its minors are fixed (proxy when Paperless is unreachable, wording, two more names in the test, docs/development.md). x_open_directory__mutmut_3 is the one removed entry whose hash still matches: the hosted run did not report it as survived; it may come back as an unreviewed survivor.

Merged as 3464077 (#113).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The weekly mutation run works again: the observation recorder knows mutmut's renamed methods, the renderer's tests are left out, the time limit is 150 minutes and eleven stale equivalents are gone. Hosted run 45296581735: 99.2 %. The proxy of the page E2E tests closes its connections.
<!-- SECTION:FINAL_SUMMARY:END -->
