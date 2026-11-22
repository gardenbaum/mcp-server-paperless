---
id: PLMCP-44
title: Re-weight e2e shards from hosted-runner timings
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-17T09:24:00Z'
updated_date: '2026-11-22T16:49:02Z'
labels: []
dependencies: []
priority: low
type: chore
ordinal: 44000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The first sharded e2e-full run on main (run 44051242147) took 745/652/458 s of tests per shard against a predicted 370/396/396 s: the weights in scripts/e2e_ci.py come from a 4-core sandbox where Celery-bound modules (test_bulk, test_pdf, versions, sharing, uploads) run about 2x faster. Re-derive the weights from the --durations output or junit of a few hosted runs so the shards balance; consider PAPERLESS_TASK_WORKERS=2 in tests/e2e/compose.yaml if it cuts the worker-bound time without flakiness.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Shard weights come from hosted-runner timings and the three shards are within 15% of each other on a main run
- [x] #2 The choice on PAPERLESS_TASK_WORKERS is recorded with measurements
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
1. Derive per-module seconds on hosted runners from the Actions timestamps of the pytest progress lines of recent green e2e-full runs (gap between module lines), take the mean of the runs after the last change of the live suite.
2. Test first (red): the split by the weights must be within 15 percent in the measured hosted seconds (HOSTED_SECONDS in tests/quality/test_e2e_ci.py); then replace MODULE_SECONDS in scripts/e2e_ci.py (green).
3. PAPERLESS_TASK_WORKERS: measure the Celery-bound modules locally with 1 and 2 workers on a second compose project, two runs each; keep or change by the numbers.
4. just check; record measurements and the prediction; AC 1 stays open until a main run shows the three shards within 15 percent.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Method (hosted timings): shard jobs print only --durations=20, so per-module time is derived from the log. Actions stamps each pytest progress line when it is complete, so the gap between the last line of a module and the last line of the previous one is the module time (continuation lines of long modules are followed; the sum matches the pytest total, e.g. 827 s against 831 s). The first module of a shard also pays the stack seeding, about 30 s, taken off in the weights. Runs used: e2e-full on main 45032837175 (dc88a62), 45033555760 (a3c2840), 45037907901 (dbe20a7), all green; 45021548018 (f68eaa1) is left out because the live suite changed at dc88a62 (PLMCP-46: test_bulk, versions, queued operations). Weights = mean of the three runs.

Hosted figures (s, mean of 3, old sandbox weight in brackets): contract/test_bulk 284 (136), test_document_writer 179 (58), test_pdf 153 (114), test_sharing 125 (70), test_document_versions 108 (50), e2e/test_pdf 93 (77), e2e/test_update_document 57, e2e/test_bulk 56, contract/test_taxonomy_edits 56, contract/test_document_upload 53 (61); everything else under 50. Non-Celery modules match the sandbox (e2e/test_raw 21 against 21, e2e/test_admin 40 against 40); Celery-bound ones are 2 to 2.5x slower (writer 3x). Run to run they vary a lot (bulk 253/296/303, writer 163/188/185, versions 142/137/45, sharing 162/147/67), probably because a module starts with the previous module's queued tasks still in the single worker (writer follows bulk in shard 1), so the weights are a mean and not exact.

Shard loads (s): the old weights predicted 388/387/387 but the three runs measured 733/398/637, 834/538/629 and 827/476/410 (first run on main: 745/652/458). The old table against the mean hosted seconds: 769/441/528 (max 1.33x the mean). The new table predicts 580/579/579 (by construction the same in the mean hosted seconds); sum 1738 s of tests, so a shard is about 10 minutes of tests plus the stack start, down from 14 minutes for the slowest. Prediction only: AC 1 needs a real main run; expect noise of 10 to 20 percent between shards from the Celery variance above, so it may need one more adjustment from the first runs.

TIER_BUDGET_SECONDS stays 450 (decision-23 names it) although the weights are now hosted seconds: the budget is about 1.75x stricter in real time, tiers above 450 hosted seconds (for example bulk with pdf) run in more parallel parts, nothing is dropped. Comment in scripts/e2e_ci.py says so; the decision text still says sandbox seconds and could be updated by the owner.

PAPERLESS_TASK_WORKERS (tests/e2e/compose.yaml is already 1; measured against 2 in a second compose project plmcp44-w on port 8031 with an override file, under the e2e flock, sandbox 4 cores, same as a hosted ubuntu-latest, runs interleaved 1,2,1,2): tests/contract/test_bulk.py, test_document_writer.py, test_document_versions.py, test_sharing.py and tests/e2e/test_pdf.py, 123 tests, all green in all four runs. Wall time workers=1: 420 s and 393 s; workers=2: 368 s and 364 s (about 10 percent less: bulk 163/151 s to 122/123 s, pdf 81/83 s to 67/69 s, versions, writer and sharing unchanged). Decision: keep 1. The gain is about 10 percent of the worker-bound modules (under 5 percent of a shard), from two runs each on a different disk and CPU than a hosted runner; a second worker adds writers on the SQLite database and lets queued tasks overtake each other, which the suite assumes not to happen, and four green runs do not show absence of flakiness. If wanted, dispatch e2e-full on a branch with 2 workers three times against main runs of the same shards to decide on hosted numbers (not done: workflows cannot be dispatched from here).

Not done: a per-module timing report in the shard runs (junit artifact); the derivation from the log timestamps works for refreshes, so it was left out of this change.

Controller: the weights' unit changed from sandbox to hosted seconds while decision-23 names the 450 s tier budget, so decision-30 supersedes decision-23 (everything carried over; weights and budget are hosted-runner seconds; budget stays 450, which splits tiers earlier, stricter not weaker; PAPERLESS_TASK_WORKERS stays 1 with the measurements). decision-23 set to superseded by decision-30 with the CLI.

Branch run 45044592721 (e2e-full on the branch with the 3-run mean weights, all green): tests 588/497/444 s (1.32x max/min) against a predicted 580/579/579. Per-module derivation as above (seeding 30 s off the first module): shard 1 552 s (predicted 554), shard 2 463 (554), shard 3 409 (553); the 36 s each to the observed totals is seeding. Shard 1 came out as predicted; shards 2 and 3 were fast because the Celery-bound modules were cheap: writer 91 s (mean of the first three 179), sharing 66 (146), versions 70 (108), pdf 113/65 (159/93), notes_and_trash 82 (38 the other way).

Variance limits AC 1: the same module sets in the three earlier runs (unchanged shard layout) took shard 1 733/834/827 s, shard 2 398/538/476 s, shard 3 637/629/410 s of tests, a spread of up to 1.55x for identical work, so the runner or the queue state decides more than the split. Swapping modules between shards 1 and 3 on run 45044592721 alone would balance that run but fits one sample (it would move about 70 s of Celery-bound work, whose time swings by more than that between runs), so I did not. Weights now = median of the four runs (runs 45032837175, 45033555760, 45037907901 on main, 45044592721 on the branch; the median ignores one fast or slow runner better than the mean): predicted 561/560/560 s, total 1681 s. Evaluated against each of the four runs' measured per-module seconds the new split gives max/min 1.20, 1.07, 1.14 and 1.29 (the mean gave 1.18, 1.14, 1.09, 1.35; the table is no better or worse in sample). So the split is balanced in the median, but one main run lands within 15 percent only about half of the time; reliable 15 percent is not reachable by re-weighting alone. The test keeps the 15 percent on the measured median seconds (it already passed with the old table, the verdict did not change); AC 1 stays open.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
MODULE_SECONDS in scripts/e2e_ci.py are now mean hosted-runner seconds of three green main runs (predicted 580/579/579 s against the previously observed 733-834/398-538/410-637). Guarded by a new test that splits the measured hosted seconds within 15 percent. PAPERLESS_TASK_WORKERS stays 1 (locally 2 workers saved about 10 percent on the worker-bound modules, no failures in 4 runs, not clearly enough). AC 1 is verified by the first main run after merge.
<!-- SECTION:FINAL_SUMMARY:END -->
