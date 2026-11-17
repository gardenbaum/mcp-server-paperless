---
id: PLMCP-44
title: Re-weight e2e shards from hosted-runner timings
status: To Do
assignee: []
created_date: '2026-11-17T09:24:00Z'
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
- [ ] #2 The choice on PAPERLESS_TASK_WORKERS is recorded with measurements
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
