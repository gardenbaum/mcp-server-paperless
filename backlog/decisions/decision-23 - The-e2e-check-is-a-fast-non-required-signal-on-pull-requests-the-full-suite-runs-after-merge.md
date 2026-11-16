---
id: decision-23
title: >-
  The e2e check is a fast, non-required signal on pull requests; the full suite
  runs after merge
date: '2026-11-16T23:32:00Z'
status: accepted
---
## Context

Decision-10 and doc-2 (and, through them, the design doc-1 section on CI) made `e2e` a required status check of `main`: the whole live suite on one paperless-ngx stack. It took 11 to 12 minutes on `main`, PR runs that added a toolset passed 20 minutes and the last green run took 32 (PLMCP-39). Every toolset adds live tests, so the job only gets slower, and it blocks every pull request, including ones that change a single toolset or only documentation.

The repository owner asked for the pull request signal to take about five minutes, and approved removing `e2e` from the required checks. The owner changes the branch protection of `main` himself: the required checks become `check (3.14)`, `check (3.15)` and `audit`. The controller's policy (doc-2) is to wait for the `e2e` run of a pull request anyway and treat red as blocking.

## Decision

This decision narrows the e2e gate and supersedes the parts of decision-10 and doc-2 that name `e2e` as required. It touches nothing else in them. The design doc-1 is not edited; this decision is the record.

- `e2e` is no longer a required check. The job keeps its name and always reports on a pull request, so it stays a visible signal and the controller can wait for it.
- On a pull request `e2e` runs a fast tier chosen from the changed paths (`scripts/e2e_ci.py scope`): the live contract suite (the contract tests on real Paperless guard the adapter and the fake), the stdio smoke test and the e2e modules of the toolsets the change touches. A source path `tools|services|paperless/<x>.py` and the tests of the same name map to `tests/e2e/test_<x>.py` (a few stems use an alias table, for example `uploads` to `test_upload_document`). Any change outside that map runs the whole suite: `server.py`, `tools/_shared.py`, `domain/`, shared adapter modules (`client`, `auth`, `errors`, `pagination`, `disposition`), e2e helpers and conftest, compose files, `pyproject.toml`, `uv.lock`, the `justfile`, workflows, scripts, vendored docs, unknown paths and an empty diff. A stem with no e2e module and no alias also runs the whole suite, so a new toolset is never skipped by accident.
- A change that touches only `backlog/`, `docs/` (not `docs/vendor/`), Markdown files at the repository root or `.github/ISSUE_TEMPLATE/` skips the live steps; the job still reports success.
- The full live suite runs after every push to `main` and nightly (`.github/workflows/e2e-full.yml`), in three shards with one stack each (`just e2e-shard K/N`; whole modules, assigned by measured duration). A red shard fails the workflow run, which GitHub shows on `main`; a red run on `main` is fixed before other work.
- Live coverage is not reduced and no test is skipped, disabled or quarantined. Consume-heavy tests whose subject is not OCR, archives, page counts or thumbnails upload plain text files instead of PDFs.

## Consequences

- Guarded on a pull request: lint, types, unit, tool and fake-backed contract tests (`check`), the live contract suite, the stdio smoke test and the live tests of the touched toolsets, as before for those parts. Guarded only after the merge: the live tests of toolsets the change does not map to.
- Risk: a regression that only an untouched toolset's live test would show (a shared behaviour the map does not know about) is found on `main`, not before the merge. It is mitigated by the whole-suite fallback for everything outside the map, the aliases for shared stems, the full run on every push to `main` and the nightly run, and by the controller waiting for the tier.
- Because `e2e` is not required, nothing in branch protection depends on the job names, and the hazards of required matrix jobs (a skipped job counting as passing) do not apply. The guard tests still keep the wiring honest.
- A new test module needs no registration: it is assigned to a shard automatically. When the balance drifts, refresh `MODULE_SECONDS` in `scripts/e2e_ci.py` from `pytest --durations`.
- Changing the non-code list, the toolset map or the shard count is a change to this gate and needs a new decision.

## Enforcement

- `scripts/e2e_ci.py` holds the rules (`select`, `assign_shards`, `workflow_problems`); `tests/quality/test_e2e_ci.py` tests them: a table of paths that must run the whole suite, the toolset and alias mapping (every alias names a real e2e module), and the skip for non-code paths.
- The same file checks the workflows: `ci.yml` job `e2e` exists, has no name or `continue-on-error`, runs the scope step and `just e2e-paths`, and only skips its steps on the scope's mode; `e2e-full.yml` runs on push to `main` and on a schedule, its matrix lists exactly shards 1/N to N/N and has no `continue-on-error`. Each rule has a case on a synthetic workflow that breaks it.
- The same file collects the real live suite whole and per shard and fails when a test is lost or selected by two shards.
- Branch protection of `main` is the owner's setting; the required checks are listed in doc-2 (manual review).
