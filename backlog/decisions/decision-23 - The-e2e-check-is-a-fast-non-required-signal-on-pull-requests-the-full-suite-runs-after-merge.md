---
superseded_by: decision-30
updated_date: '2026-11-22T16:30:06Z'
id: decision-23
title: >-
  The e2e check is a fast, non-required signal on pull requests; the full suite
  runs after merge
date: '2026-11-16T23:32:00Z'
status: superseded
---
## Context
<!-- SECTION:CONTEXT:BEGIN -->

The live suite (live contract suite and E2E tests against paperless-ngx 3.4.2) took 11 to 12 minutes on `main`; it passed 20 minutes with PLMCP-22 and PLMCP-24, 32 minutes at the last green run and about 40 minutes with every toolset (pytest alone 33:54 on a hosted runner; a run was cancelled at 35 minutes and PLMCP-43 raised the job's `timeout-minutes` to 60 as a stopgap). Every toolset adds live tests, so it only gets slower (PLMCP-39). Measured on a 4-core sandbox, the plain-text documents already in place, the whole suite takes 1163 s of tests (629 tests): 716 s in `tests/contract`, 446 s in `tests/e2e`.

Decision-24 removed `e2e` from the required checks (the owner did that in the branch protection settings) and runs the suite after every merge to `main`. It announced a fast tier for pull requests of about five minutes and a nightly full run for PLMCP-39. This decision is that part. The controller accepted it on 2026-11-17 under the owner's delegation; the owner asked for PLMCP-39 to be implemented and was informed of the decision. Nothing in it makes a check required.

<!-- SECTION:CONTEXT:END -->
## Decision
<!-- SECTION:DECISION:BEGIN -->

**Supersedes decision-24.** Decision-24's Decision and Enforcement put the full suite in the `e2e` job of ci.yml on every push to main; this decision moves it to `e2e-full.yml` and makes the ci.yml `e2e` job pull-request only, so decision-24 cannot stay as written.

Carried over from decision-24: `e2e` stays out of the required checks (`check (3.14)`, `check (3.15)`, `audit`, as in doc-2; branch protection is the owner's setting); the full suite runs after every merge to main; a regression only the live suite catches is found there and fixed as the next pull request. Changed: the full suite runs in `e2e-full.yml` in three shards (also nightly and on demand), the `e2e` job of ci.yml runs the fast tier on pull requests, and the enforcement lines below replace decision-24's. The design doc-1 is not edited.

- What runs on a pull request: the `e2e` job of ci.yml runs a fast tier chosen from the changed paths (`scripts/e2e_ci.py scope`): the stdio smoke test plus the live contract and e2e modules of the toolsets the change touches. A source path `tools|services|paperless/<x>.py` and the unit tests of the same name map to `tests/contract/test_<x>.py` and `tests/e2e/test_<x>.py` where those exist, and through an alias table to the modules of related stems (for example `uploads` to `test_document_upload` and `test_upload_document`). `tools/core.py` maps to the live modules of documents, notes, files, search, tasks, taxonomy and the instance overview, and `domain/<x>.py` like the other layers. The entry points (`app`, `cli`, `server`) and the shared adapter modules (`client`, `auth`, `errors`, `pagination`, `disposition`) run the smoke set plus the modules their stem names (`test_http`, `test_client`). A change outside that map (`tools/_shared.py`, `domain/ports.py`, helpers and conftest files, compose files, `pyproject.toml`, `uv.lock`, the `justfile`, workflows, scripts, vendored docs, a stem with no module and no alias, unknown paths, an empty diff) adds a small smoke set of about 30 s (the stdio test, the documents e2e module and the read-only instance, task and taxonomy contract modules). It never runs the whole suite on a pull request: that is what the run after the merge is for. A tier heavier than 450 s of recorded test time (a change across many toolsets: documents, bulk, pdf, uploads, versions and sharing together are about 800 s in the sandbox) is split over the three parts of the job's matrix by the same weights, about 400 s each and under 15 minutes hosted; no module is dropped. The tier is a signal of a few minutes (2 to 10 on a hosted runner; the heaviest single toolsets, `bulk` and `pdf`, are about 3 minutes of tests in the sandbox, 5 to 6 on a hosted runner, plus the stack's start).
- The job always runs and reports, so the controller can wait for it; a change touching only `backlog/`, `docs/` (not `docs/vendor/`), Markdown files at the repository root or `.github/ISSUE_TEMPLATE/` skips the live steps and the job still succeeds. It is not a required check. The controller's policy (doc-2, as decision-24 announced) is to wait for it and to fix a red tier before merging; GitHub does not enforce that.
- The full live suite runs after every push to `main`, nightly and on demand (`.github/workflows/e2e-full.yml`), in three shards with one stack each (`just e2e-shard K/N`; whole modules, assigned by measured duration with the weights in `scripts/e2e_ci.py`). That replaces the single `e2e` job on `main` of decision-24: the push trigger moved from ci.yml to this workflow, the rule that the full suite runs after each merge is unchanged. A red shard fails the workflow run, which GitHub shows on `main`; a red run on `main` is fixed before other work.
- Hang guards (they bound a stuck run, they are no quality gate): each part of the tier's job 20 minutes, a shard 25 minutes. PLMCP-43's 60 minutes for the single full job is gone with that job. The expected times are 2 to 10 minutes for the tier and about 15 for a shard (about 390 s of tests per shard in the sandbox, 1.75 times as long on a hosted runner, plus about 2 minutes to start the stack); the three shards run in parallel, so the full run is under 20 minutes.
- Live coverage is not reduced and no test is skipped, disabled or quarantined. Consume-heavy tests whose subject is not OCR, archives, page counts or thumbnails upload plain text files instead of PDFs.

<!-- SECTION:DECISION:END -->
## Consequences
<!-- SECTION:CONSEQUENCES:BEGIN -->

- Guarded on a pull request: lint, types, unit, tool and fake-backed contract tests (`check`), and the live tests of the toolsets the change touches. Guarded only after the merge: the live tests of every toolset the change does not map to, and a shared change beyond the smoke set.
- Risk: a regression that only an untouched toolset's live test would show (a shared behaviour the map does not know about) is found on `main`, not before the merge. It is mitigated by the alias table, the smoke set for everything outside the map, the full run on every push to `main` and the nightly run, and by the controller waiting for the tier. A shared change is the weakest case: the smoke set is small.
- Because `e2e` is not required (carried over from decision-24), nothing in branch protection depends on the job names; the hazards of required matrix jobs (a skipped job counting as passing) do not apply. The guard tests still keep the wiring honest.
- A new test module needs no registration: it is assigned to a shard automatically (an unweighted module counts as the average). When the balance drifts, refresh `MODULE_SECONDS` in `scripts/e2e_ci.py` from `pytest --junitxml`; a test fails when the split of the table is uneven by more than 15 %.
- Each shard has its own stack on its own runner, so the container lookup of `rebuild_search_index` (PLMCP-40: `docker ps --filter publish=<port>`) finds one container per run; locally a second stack needs its own compose project and port.
- Changing the non-code list, the tier budget, the toolset map, the smoke set, the shard count or the timeouts is a change to this gate and needs a new decision.

<!-- SECTION:CONSEQUENCES:END -->
## Enforcement

- `scripts/e2e_ci.py` holds the rules (`select`, `assign_shards`, `workflow_problems`); `tests/quality/test_e2e_ci.py` tests them: a table of paths that run the smoke set, the toolset and alias mapping (every alias names a real live module), the smoke set exists and stays small, every live module is reachable from a source path or the smoke set, an over-budget tier is split without dropping a module, and the skip for non-code paths.
- The same file checks the workflows: `ci.yml` job `e2e` exists, has no name or `continue-on-error`, runs the scope step and `just e2e-paths`, lists the parts 1/3 to 3/3, passes the part to the scope step, only skips its steps on the scope's mode and has `timeout-minutes` of at most 20; `e2e-full.yml` runs on push to `main` and on a schedule, its matrix lists exactly shards 1/N to N/N, it has no `continue-on-error` and `timeout-minutes` of at most 25. Each rule has a case on a synthetic workflow that breaks it.
- The same file collects the real live suite whole and per shard and fails when a test is lost or selected by two shards, and checks that the weights name existing modules and split the suite evenly.
- Branch protection of `main` is the owner's setting; the required checks are listed in doc-2 and decision-24 (manual review).
