---
updated_date: '2026-11-24T20:59:51Z'
id: decision-76
title: The live suite runs in six shards and a pull-request tier in up to six parts
date: '2026-11-24T20:21:58Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Decision-58 splits about 2970 s of live tests over four shards of 25 minutes. PLMCP-79.x and 80.x brought the suite to about 4300 s (median of the e2e-full runs 45460920436, 45467934032, 45467975266 and 45475116464 of 2026-11-24). Shard 1 ran into the guard on the branch head 957046b and on main ff22aa3 (run 45475116464) although no test had failed. Evenly split, four shards take about 1090 s each, five 873 s; runners vary by about 1.5. The cross-cutting tier has 2412 s: four parts of 450 s do not hold it.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes of decision-58 only the number of shards and parts, the expected seconds and the measured runs:**

- The full live suite (`e2e-full.yml`, `e2e-latest.yml`) runs in six shards, about 730 s of tests each, plus about 2 minutes for the stack.
- A pull-request tier heavier than 450 s is split over up to six parts of the `e2e` matrix.
- The tier budget (450 s), the hang guards (20 and 25 minutes), the worker count and everything else of decision-30 and decision-58 stay. No test is skipped or dropped.
- `MODULE_SECONDS` and `HOSTED_SECONDS` are the median of the four runs above. Three are of main; 45467975266 is of a branch whose tree equals main ff22aa3; of 45475116464 the modules shard 1 did not finish are missing.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Two more runners per full run and per pull request; with several pull requests at once jobs can queue longer.
- The owner approved six shards and six parts on 2026-11-24 ("Alles wie von dir empfohlen.", everything as you recommend).
- `tests/contract/test_pdf.py` alone takes 440 s, close to the tier budget: when it grows, it has to be split into two modules.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_e2e_ci.py`: the matrices list exactly the shards and parts of `SHARDS` and `TIER_PARTS`; the split is even within 15 % in the measured hosted seconds; the cross-cutting tier fits the budget in every part.
