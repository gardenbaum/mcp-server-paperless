---
updated_date: '2026-11-24T09:05:34Z'
id: decision-58
title: The live suite runs in four shards and a pull-request tier in up to four parts
date: '2026-11-24T09:05:16Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Decision-30 (and decision-23, which it carries over) names three shards of the full live suite and three parts of the pull-request tier, for 1738 s of tests. The suite has grown to about 2970 s (median of the e2e-full runs 45349506723, 45349964929, 45353668848 and 45355735636 of 2026-11-24). With the old weights one shard took 20 to 22 minutes while the others took 12 to 19, and run 45357325758 hit the hang guard of 25 minutes at 97 % of shard 3 although nothing hung. With the new weights the cross-cutting tier of the tests (documents, bulk, pdf, uploads, versions, sharing: 1575 s) no longer fits three parts of 450 s. Decision-30 says that a change of the shard count needs a new decision.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes the number of shards and parts of decision-30 and nothing else of it:**

- The full live suite (`e2e-full.yml`, `e2e-latest.yml`) runs in four shards, about 745 s of tests each, plus about 2 minutes for the stack.
- A pull-request tier heavier than 450 s is split over up to four parts of the `e2e` matrix.
- The tier budget (450 s), the hang guards (20 and 25 minutes), the worker count and everything else of decision-30 stay. No test is skipped or dropped.
- `MODULE_SECONDS` and `HOSTED_SECONDS` are refreshed from the four runs above.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- One more runner per full run; with several pull requests at once jobs can queue longer.
- The suite can grow by about a third before a shard comes near the guard again. Then the weights or the shard count change again, not the guard.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_e2e_ci.py`: the matrices list exactly the shards and parts of `SHARDS` and `TIER_PARTS`; the split of `MODULE_SECONDS` is even within 15 % in the measured hosted seconds; the cross-cutting tier fits the budget in every part.
