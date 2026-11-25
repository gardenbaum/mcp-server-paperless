---
id: decision-112
title: The live suite runs before a release only
date: '2026-11-25T19:19:32Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The live suite takes up to 25 minutes in six shards plus four more jobs, after every merge, every night and, as a tier in six parts, on every pull request. With several pull requests at once the runner queue grew to hours and merges waited for it. The owner decided on 2026-11-26: "Du musst einen notfall PR machen um ci abzukürzen. e2e nur noch vor dem tag/build." (You have to make an emergency PR to shorten CI. E2E only before the tag/build.)
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes of decision-23, decision-30, decision-58 and decision-76 when the live suite runs; the shards, weights, guards and worker count stay.**

- No pull request, no merge to main and no schedule runs the live suite against the pinned release. The `e2e` job of ci.yml is gone.
- `e2e-full.yml` runs only when dispatched: on the commit of main that is going to be tagged.
- `pypi.yml` and `image.yml` publish a version tag only when a run of `e2e-full.yml` has passed on exactly the tagged commit. Their other jobs on pull requests are unchanged. `registry.yml` waits for PyPI and needs no gate of its own.
- The weekly `e2e-latest.yml` (newest paperless-ngx 3.x) stays: it delays no merge.
- The tier selection of `scripts/e2e_ci.py` stays for use by hand (`just e2e-paths`).
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A regression only a live Paperless shows is found before a release, together with those of other merges, not at its own merge. New live tests run for the first time then.
- A release takes one more step: dispatch, wait, tag. A red run is fixed before the tag.
- Unit, tool, fake-backed contract and adapter tests, lint, types, the coverage of the diff and the audit still run on every pull request.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_e2e_ci.py`: ci.yml runs no live test; `e2e-full.yml` offers only `workflow_dispatch` and lists every shard.
- `tests/quality/test_pypi.py` and `tests/quality/test_container.py`: both publish jobs need the gate, and the gate looks for a passed run on the tagged commit.
