---
id: decision-22
title: The e2e check is sharded and skipped for non-code changes
date: '2026-11-16T23:24:00Z'
status: accepted
---
## Context

`e2e` is a required status check of `main` (doc-2) that ran the whole live suite on one paperless-ngx stack: 11 to 12 minutes on `main`, but PR runs that added a toolset passed 20 minutes and the last green run took 32. The timeout was raised to 35 minutes as a stopgap (PLMCP-39). Two things make the job long: the suite grows with every toolset, and it runs for pull requests that change only Backlog files, docs or Markdown, which cannot change what a live test sees.

Branch protection requires the check names `check (3.14)`, `check (3.15)`, `audit` and `e2e`, and must not change. GitHub counts a skipped required check (a job whose `if` is false, or whose `needs` failed) as passing, so a naive split into parallel jobs, or a path filter on the job, would let a red or missing shard merge.

## Decision

The repository owner approved this change to the required `e2e` gate (PLMCP-39). It narrows what the gate runs for documentation-only pull requests; it does not weaken it for code.

- `e2e` stays one required check with the same name, but is an aggregate job that always runs (`if: always()`, never skipped) and needs two other jobs. It fails unless the scope job succeeded and either every shard succeeded or the scope decided to skip and the shards were skipped. A failed, cancelled or skipped shard fails it.
- `e2e-scope` decides whether the live suite must run. Pushes to `main` always run it. For a pull request it lists the changed paths (the pull request's own commits and the merge result against the base, renames listed on both sides) and skips only when every path is plainly non-code: `backlog/`, `docs/` except `docs/vendor/`, Markdown files at the repository root, and `.github/ISSUE_TEMPLATE/`. Everything else runs the suite: `src/`, `tests/`, `scripts/`, `pyproject.toml`, `uv.lock`, the `justfile`, `tests/e2e/compose*`, `.github/workflows/`, `docs/vendor/` (the adapter tests validate against the vendored schema), Markdown below `src/` (runtime data), unknown files, and an empty diff.
- `e2e-shard (K/N)` jobs run `just e2e-shard K/N`, each on its own stack. A shard is a set of whole test modules, assigned heaviest first to the lightest shard by measured duration (`MODULE_SECONDS` in `scripts/e2e_ci.py`; an unmeasured module counts as the average), so the assignment depends only on the module paths. The shards partition the live suite exactly; `just e2e` still runs all of it locally.
- Live coverage is not reduced: no test is skipped, disabled or quarantined. Consume-heavy tests whose subject is not OCR, archives, page counts or thumbnails upload plain text files instead of PDFs.

## Consequences

- Documentation-only pull requests get a green `e2e` in seconds, without a stack. A path that is wrongly classified as non-code is a way to skip live tests, so the list is an allow-list and anything unknown runs the suite.
- Three stacks run in parallel, so a code pull request uses three runners; wall-clock time is about the slowest shard plus its startup.
- A new test module needs no registration: it is assigned automatically. When the balance drifts, refresh `MODULE_SECONDS` from `pytest --durations` output.
- The required check name `e2e` is unchanged, so branch protection needs no edit. Renaming the jobs `e2e` or removing `if: always()` from it would reopen the skipped-check hole.
- Changing the non-code list, the aggregate rule or the shard count means a new decision that supersedes this one.

## Enforcement

- `scripts/e2e_ci.py` holds the rules (`needs_e2e`, `verdict`, `assign_shards`, `workflow_problems`); `tests/quality/test_e2e_ci.py` tests each of them, including a table of paths that must run the suite and of verdicts that must fail (skipped, cancelled and failed shards, a missing or malformed scope result).
- `tests/quality/test_e2e_ci.py` checks `.github/workflows/ci.yml`: the `e2e` job has `if: always()`, needs exactly `e2e-scope` and `e2e-shard`, has no name or `continue-on-error`; the shard job needs the scope and its matrix lists exactly shards 1/N to N/N. Each rule has a case on a synthetic workflow that breaks it.
- The same file collects the real live suite whole and per shard and fails when a test is lost or selected by two shards.
- Branch protection of `main` (required check `e2e`) is unchanged; the guard on it is doc-2 and manual review.
