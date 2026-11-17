---
id: decision-24
title: E2E is no longer a required pull request check
date: '2026-11-16T23:40:00Z'
status: superseded by decision-23
---
## Context

The full e2e suite (live contract suite and E2E tests against paperless-ngx 3.4.2) grew to about
30 minutes per run and made every pull request wait. It was one of the four required checks on
main. The owner decided on 2026-11-16 that the slow suite must not gate pull requests and removed
`e2e` from the required checks himself in the branch protection settings.

## Decision

- The required checks on main are `check (3.14)`, `check (3.15)` and `audit`.
- The CI `e2e` job runs the full suite on every push to main (that is, after every merge), not on
  pull requests.
- PLMCP-39 adds a fast e2e tier of about five minutes for pull requests and a nightly full run.
  The controller waits for that tier before merging once it exists; until then the implementer's
  local e2e run of the touched toolset (doc-2) is the live evidence on a pull request.

## Consequences

- Pull requests no longer wait about 30 minutes for e2e.
- A regression that only the live suite catches is found on main after the merge, not before.
  The run after each merge makes it visible at once; the fix goes in as the next pull request.
- Decision-8 (test strategy) still holds for what the suites cover; only when the live suite gates
  changes.

## Enforcement

- `.github/workflows/ci.yml`: the `e2e` job has `if: github.event_name == 'push'`.
- Branch protection on main lists only `check (3.14)`, `check (3.15)` and `audit`.
- doc-2 names the required checks and the local e2e evidence.

Superseded by decision-23: the full suite now runs in `e2e-full.yml` after every merge to main, and the ci.yml `e2e` job runs a fast tier on pull requests.
