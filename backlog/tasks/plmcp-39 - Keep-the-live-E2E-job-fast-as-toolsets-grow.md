---
id: PLMCP-39
title: Keep the live E2E job fast as toolsets grow
status: Done
assignee:
  - '@claude'
created_date: '2026-11-13T07:29:00Z'
updated_date: '2026-11-17T08:31:00Z'
labels:
  - ci
  - testing
dependencies: []
priority: medium
ordinal: 39000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The e2e CI job runs the live contract suite and the E2E tests against one paperless-ngx 3.4.2 stack. main took 11-12 min; PR runs adding one toolset (PLMCP-22 bulk, PLMCP-24 versions) exceeded the 20-minute job timeout, with worker-bound modules (document_writer, notes_and_trash, document_upload) about 2x slower than on main runs. The timeout was raised to 35 minutes as a stopgap. Find where the time goes and bring the job back well under 20 minutes without dropping live coverage.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Per-module timings of the e2e job are measured on CI and recorded in the notes, including why worker-bound modules vary 2x between runs
- [x] #2 Consume-heavy live tests use plain text documents where OCR, archives, page counts or thumbnails are not what they test
- [ ] #3 The e2e job (or its split into parallel jobs with their own stacks, if the owner approves the required-check change) finishes in under 20 minutes on main
- [x] #4 The job timeout is lowered back once the suite is fast again
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Resolve the merge with main: keep PLMCP-43's context, decision-24 and doc-2's narrowed gates; doc-2 only through the CLI.
2. Measure the suite per module (junit durations) and replace the whole-suite fallback of the PR tier: the live contract suite alone is 716 s of 1163 s, so the PR tier runs per-toolset contract and e2e modules plus the stdio test, and a small smoke set for changes outside the map.
3. Weights for the shard split from the measurement; guard tests for timeouts, weights and smoke set (red first).
4. Lower the timeouts: PR tier 20 min, shards 25 min (PLMCP-43's 60 min guarded the single job that no longer exists).
5. decision-23 rewritten and set to proposed (owner has not accepted it); doc-2 mentions the tier through the CLI.
6. Verify: just check, full suite once, one shard end to end on a second stack, the tier on sample paths.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Paused on 2026-11-17 by the owner's call: PLMCP-32 goes first. WIP is on branch plmcp-39-e2e-speed (fast PR tier, sharded full run after merge, plain-text documents in consume-heavy tests, draft decision-23 building on decision-24); the baseline measurement did not finish. Resume from that branch.

Resumed 2026-11-17 and merged origin/main (conflicts in ci.yml and doc-2; main's PLMCP-43, decision-24 and doc-2 text kept, doc-2 changed only through the CLI).

Measurements (sandbox, 4 cores, plain-text documents already in place, full suite once, junit durations): 629 passed, 6 skipped in 1163 s of tests (19:28 wall clock). tests/contract 716 s, tests/e2e 446 s. Heaviest modules: contract/test_bulk 136 s, contract/test_pdf 114, e2e/test_pdf 77, contract/test_sharing 70, contract/test_document_upload 61, contract/test_document_writer 58, contract/test_document_versions 50. Main's hosted-runner run was 33:54 for pytest, so a hosted runner is about 1.75 times slower than the sandbox. The table is MODULE_SECONDS in scripts/e2e_ci.py. NOT measured: per-module times on a hosted runner (no CI access from here); the new jobs print --durations=20, so the first runs on main give them. The 2x spread of worker-bound modules is not proven; likely cause: the stack runs PAPERLESS_TASK_WORKERS=1 on a 2-vCPU runner, so consume, OCR and index-writing tasks queue serially and a module's time depends on what else is queued (the plain-text documents shorten each task, which is why they help).

Changes beyond the WIP: (1) the PR tier no longer runs the whole live contract suite (716 s alone, 20+ min hosted): it runs the stdio test plus the contract and e2e modules of the touched toolsets (alias table extended to contract modules), and a change outside the map adds a ~30 s smoke set instead of the whole suite. Tier locally: sharing change 40 tests in 96 s; smoke set 32 tests in 38 s. (2) Timeouts: the PR job 20 min (was PLMCP-43's 60 on the single job; the WIP draft had 35), the shard jobs 25 min; both guarded by tests/quality/test_e2e_ci.py. (3) Weights filled; the test requires the split of the real suite to be within 15 percent of even (shards 370/396/396 s). (4) decision-23 rewritten and set to proposed: nothing records the owner's acceptance of it. doc-2 now names the tier and the full run. Nothing is made a required check.

Shard evidence: shard 2/3 ran end to end on a second compose project (port 8021): 135 passed in 385 s (predicted 396). The PLMCP-40 test (more_like, whose rebuild_search_index does docker exec into the container publishing PORT) passed on that stack with two stacks up, so the lookup by published port works; in CI each shard has its own runner and stack. Throttling of /api/token/ is per stack, so shards do not share it. Second stack removed afterwards.

Estimates, not measured: full run on main = 3 parallel shards, about 390 s of tests each, about 11-12 min hosted plus ~2 min stack start, so ~14 min (AC 3 stays unchecked until a run on main shows it). PR tier 2-10 min hosted.

Controller: accepted decision-23 under the owner's standing delegation (2026-11-17, owner asked to implement PLMCP-39). Added persist-credentials: false to the PR e2e checkout (it only diffs, never pushes). AC #1 and #3 are verified by the first sharded runs on main; shard weights and timeouts get one follow-up adjustment if those runs disagree with the estimates.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Live E2E now splits in two. Pull requests run a fast, non-required tier (stdio test plus the contract and e2e modules of the touched toolsets; a small smoke set for changes outside the map; skipped for docs and backlog only); main and nightly run the whole suite in three parallel shards (e2e-full.yml). Hang guards lowered from PLMCP-43's 60 min: 20 min for the PR tier, 25 min per shard. Consume-heavy live tests use plain text documents. Measured locally: 1163 s of tests in all, shards 370/396/396 s, one shard run end to end (135 passed, 385 s). Not verified: hosted-runner timings, so AC 1 and 3 stay open until the first runs on main; DoD 2-4 need the PR. Risks: a shared change is checked on the PR only by the smoke set and fully after the merge; decision-23 is proposed and needs the owner's acceptance; decision-24's enforcement line (e2e job on push) is carried out by e2e-full.yml.
<!-- SECTION:FINAL_SUMMARY:END -->
