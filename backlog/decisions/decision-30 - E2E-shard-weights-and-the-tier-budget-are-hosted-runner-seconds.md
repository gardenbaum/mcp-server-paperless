---
updated_date: '2026-11-22T16:30:05Z'
id: decision-30
title: E2E shard weights and the tier budget are hosted-runner seconds
date: '2026-11-22T16:30:05Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Decision-23 split the full live suite into three shards and the pull-request tier into up to three parts, both by the weights in `scripts/e2e_ci.py`, and named a tier budget of 450 s of recorded test time. Those weights were seconds of a 4-core sandbox. On hosted runners the Celery-bound modules (bulk, document writer, pdf, sharing, versions) take two to three times as long, so the first sharded main runs were uneven (733/398/637 s, 834/538/629 s, 827/476/410 s against a predicted 388/387/387 s). PLMCP-44 re-derived the weights from three green e2e-full runs on main as hosted-runner seconds. Decision-23 says a change to the tier budget needs a new decision; keeping the number 450 while its unit changes from sandbox to hosted seconds is such a change, so this decision states it.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes decision-23.** Everything in decision-23 is carried over unchanged (the non-required `e2e` tier on pull requests chosen from the changed paths, the toolset and alias map, the smoke set, the skip for non-code paths, the full suite in three shards after every push to main, nightly and on demand, the hang guards of 20 and 25 minutes, no test skipped or dropped, its consequences and enforcement), except:

- The weights in `scripts/e2e_ci.py` (`MODULE_SECONDS`) are seconds of tests on a hosted runner (ubuntu-latest), measured from e2e-full runs on main. They are refreshed from hosted runs, not from a local machine; `HOSTED_SECONDS` keeps the measured table the split is checked against.
- The tier budget stays 450 s, now of hosted-runner test time. A tier heavier than that is split over the parts of the `e2e` matrix as before. That splits tiers earlier than under decision-23 (450 hosted seconds are about 260 sandbox seconds): a tier runs in more parallel jobs, nothing is dropped, and each part stays well inside the 20-minute guard.
- The expected full run is about 580 s of tests per shard on a hosted runner (1738 s in total at PLMCP-44), plus about 2 minutes for the stack, inside the 25-minute shard guard.
- `PAPERLESS_TASK_WORKERS` stays 1: two workers saved about 10 % of the Celery-bound modules locally (420/393 s against 368/364 s), under 5 % of a shard, and would add a second SQLite writer and let queued tasks overtake each other.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The shards balance in the unit they are run in; the Celery-bound modules still vary by 10 to 20 % between runs, so the 15 % target of a main run can need one more refresh.
- Pull-request tiers of several toolsets split into parallel parts sooner; they finish sooner and use more runners.
- Changing the budget, the shard count, the timeouts or the worker count still needs a new decision.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- Everything decision-23 lists under Enforcement: `scripts/e2e_ci.py` holds the rules and `tests/quality/test_e2e_ci.py` tests them and the workflows.
- `test_the_split_by_the_weights_is_even_in_hosted_runner_seconds` in the same file fails when the split of `MODULE_SECONDS` is uneven by more than 15 % in the measured hosted seconds (`HOSTED_SECONDS`).
- `PAPERLESS_TASK_WORKERS` in `tests/e2e/compose.yaml` is reviewed against this decision (manual review).
