---
id: decision-114
title: The live suite runs in ten shards
date: '2026-11-26T15:20:56Z'
status: accepted
---

## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-76 runs the live suite in six shards of about 730 s each. On 2026-11-25 six shards took 11 to 23 minutes and shard 1 hit the 25-minute hang guard (run 45670381272). Under decision-112 a release needs one green full run, so a shard that hits the guard blocks it. Owner, 2026-11-26: "Bitte e2e aufteilen damit das zuverlässig geht." (Please split the e2e so that it runs reliably.)
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes of decision-76 only the number of shards of the full suite:**

- The full live suite (`e2e-full.yml`, `e2e-latest.yml`) runs in ten shards (`SHARDS = 10`), about 10 minutes each with the stack. The hang guard stays at 25 minutes.
- The pull-request tier, the weights and the measured runs of decision-76 are unchanged.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Four more runners per full run, each with about 2 minutes for its stack.
- A shard can run twice as slow as weighed and still end within the guard.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_e2e_ci.py`: the matrices list exactly the shards of `SHARDS`; the split is even within 15 % in the weights and in the measured hosted seconds.
