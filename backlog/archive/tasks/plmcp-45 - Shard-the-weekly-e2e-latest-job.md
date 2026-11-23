---
id: PLMCP-45
title: Shard the weekly e2e-latest job
status: Done
assignee:
  - '@claude'
created_date: '2026-11-22T11:25:00Z'
updated_date: '2026-11-22T12:11:00Z'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 45000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The weekly E2E run against the latest paperless-ngx 3.x (.github/workflows/e2e-latest.yml, just e2e-latest) still runs the whole suite in one job with timeout-minutes 30. Its last scheduled run (2026-11-16, run on e2e-latest.yml) was cancelled at the limit; the suite takes about 33 minutes of pytest in one hosted job (see PLMCP-39). The next Monday run will be cancelled too, so the upstream-drift signal (design 9.2) is lost. Split it like e2e-full.yml: resolve the version once, then the shards of scripts/e2e_ci.py each on its own stack against that version.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 e2e-latest.yml resolves the paperless-ngx version once and runs every shard 1..SHARDS against it on its own stack, fail-fast off, with a per-shard hang guard like e2e-full
- [x] #2 just e2e-latest takes an optional shard and still runs the whole suite without one
- [x] #3 scripts/e2e_ci.py workflow checks (tests/quality/test_e2e_ci.py) cover e2e-latest.yml: matrix, schedule, timeout and that every shard uses the resolved version
- [x] #4 A workflow_dispatch run of e2e-latest on main after the merge is green, or its failures are real upstream changes recorded as tasks
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
1. Tests first: extend tests/quality/test_e2e_ci.py with e2e-latest checks and mutated-workflow cases (red).
2. scripts/e2e_ci.py: add latest-workflow checks (matrix, schedule, timeout <=25, resolved version via needs.resolve.outputs, no continue-on-error).
3. justfile: e2e-latest takes optional shard; add e2e-latest-version recipe.
4. e2e-latest.yml: resolve job + shard matrix, per-shard artifacts.
5. just check; run shard 3/3 locally under flock.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first: tests/quality/test_e2e_ci.py gained GOOD_LATEST, 21 mutated e2e-latest cases and the real-workflow check (49 red), then scripts/e2e_ci.py (workflow_problems now takes the latest workflow as a third argument; existing callers updated) went green. e2e-latest.yml: resolve job (just e2e-latest-version) plus a 3-shard matrix job, fail-fast off, 25 min guard, per-shard log artifacts. justfile: e2e-latest-version and e2e-latest [shard]. No gate, snapshot or decision weakened. Evidence: just check green (4399 passed, diff coverage 100 %); PAPERLESS_E2E_VERSION=3.4.2 just e2e-latest 3/3 under flock: 188 passed, 1 skipped in 7m51s locally.

AC #4: manual run 45018761656 on main. The resolve job picked 3.5.1 in 14 s and all three shards ran against it (8:53, 8:29, 10:31 wall time, all well inside the 25-minute guard). The shards failed 35 tests in all; these are real upstream changes in paperless-ngx 3.5.1 (the pinned 3.4.2 suite is green nightly), recorded as PLMCP-46.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Split e2e-latest.yml into a resolve job (version once, via the new just e2e-latest-version recipe) and a 3-shard matrix running just e2e-latest K/3 against it, with fail-fast off and a 25-minute guard per shard. just e2e-latest takes an optional shard. scripts/e2e_ci.py checks the workflow (matrix, schedule, timeout, needs.resolve.outputs version, no continue-on-error), with mutation tests. AC 4 is verified after the merge by a dispatched run.
<!-- SECTION:FINAL_SUMMARY:END -->
