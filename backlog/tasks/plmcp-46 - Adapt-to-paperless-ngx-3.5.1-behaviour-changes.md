---
id: PLMCP-46
title: Adapt to paperless-ngx 3.5.1 behaviour changes
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-22T12:11:00Z'
updated_date: '2026-11-22T15:01:37Z'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 46000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The first sharded e2e-latest run (manual, run 45018761656, 2026-11-22) resolved paperless-ngx 3.5.1 and failed 35 live tests; the same suite is green on the pinned 3.4.2 every night. The earlier weekly runs were cancelled by their timeout, so this drift was not visible before.

Failing modules (count): contract/test_bulk 12, contract/test_pdf 10, contract/test_document_versions 3, e2e/test_bulk 3, e2e/test_pdf 2, and one each in contract/test_admin, contract/test_notes_and_trash, e2e/test_mail, e2e/test_taxonomy_edits, e2e/test_versions.

Patterns:
- Bulk edits look unapplied when read back (custom fields {} instead of the set values; tags without parents and children). Possibly bulk_edit became asynchronous or changed its wait semantics in 3.5.
- PDF operations (rotate, merge, edit/split) report no new documents or versions; the e2e main flow sees task status 'unknown'.
- Version merges keep fewer versions than expected.
- test_admin: reading the app configuration is now denied for a non-admin user.
- test_notes_and_trash: Paperless now rejects empty or blank notes.

Steps: refresh the vendored Paperless docs and openapi.json to 3.5.x (just docs-sync-paperless) and diff them; read the 3.5 release notes; reproduce locally with PAPERLESS_E2E_VERSION=3.5.1 just e2e-latest; fix the adapter where the API changed and keep 3.4.2 working (the pinned version) unless a decision moves the pin. Logs: download the shard job logs of the run above.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each of the 35 failures is explained in the notes as an adapter bug, an intentional upstream change, or a test assumption
- [ ] #2 The adapter handles both 3.4.2 and 3.5.x where the API differs, with contract tests on the fake for both behaviours
- [ ] #3 A manual e2e-latest run against 3.5.1 is green and the pinned 3.4.2 suite stays green
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Find the upstream change: 3.5.1 container source and OpenAPI diff against docs/vendor (no vendor edits). Finding: every document operation (bulk_edit, delete, reprocess, rotate, merge, merge_as_versions, edit_pdf, remove_password) now only queues a bulk_update task and answers {result, task_id}; failures show only in the task.
2. Adapter: new paperless/queued.py (TaskWaiter) follows the named task until it ends; wire it into PaperlessBulkWriter, PaperlessPdfWriter (fresh window for the consume tasks, failed task -> the 3.4 error) and the version merge. 3.4.2 answers carry no task_id and are unchanged.
3. Intentional upstream changes: app config read needs view_applicationconfiguration (adapter gives a hint), blank notes rejected, mail password mask fixed at 10 chars, tag delete refused when it would take tags of others; make contract/e2e expectations version-aware.
4. Tests first at adapter level (both wire formats), version-aware contract and e2e tests; just check; live modules on 3.5.1 and 3.4.2; mutation on the changed modules.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause and classification of the 35 failures (paperless-ngx 3.5.1 vs 3.4.2):

A. Queued document operations (31 failures; adapter bug, changed API). Probed in the 3.5.1 container (documents/views.py, bulk_operations.py): bulk_edit, delete, reprocess, rotate, merge, merge_as_versions, edit_pdf and remove_password no longer do the work in the request. They queue a bulk_update task (execute_bulk_operation) and answer {"result":"OK","task_id":...}; the change, the consume_file tasks of PDF operations and every failure show up only later in that task. The OpenAPI diff against docs/vendor confirms task_id in all eight results. 3.4.2 does the work in the request (no task_id).
- contract/test_bulk 12 and e2e/test_bulk 3: read-back before the change (tags, fields, references, trash). 3 of the 12 use a restricted user without view_paperlesstask, who cannot follow the task: see the grace below.
- contract/test_pdf 10 and e2e/test_pdf 2: the 3 s window for consume tasks ran out before the bulk_update task had even run (task status unknown); 3 of them (bad edit, wrong password, text file) expected the 3.4 plain-text 400, which is now a failed task.
- contract/test_document_versions 3 and e2e/test_versions 1: merge_as_versions is queued too.
(12+3+10+2+3+1 = 31.)

B. Intentional upstream changes (4 failures; the adapter was right, expectations changed):
- contract/test_admin 1: reading /api/config/ needs view_applicationconfiguration (3.4: nothing asked for a GET). The adapter now turns the 403 into a PermissionDenied that says so.
- contract/test_notes_and_trash 1: blank and empty notes are rejected with 400 "This field may not be blank." (the service already rejects them itself).
- e2e/test_mail 1: the mail password mask is always 10 asterisks (3.4: one per character); upstream stopped leaking the length. Test assumption.
- e2e/test_taxonomy_edits 1: deleting a tag is refused (403) when it would take child tags of other users with it (3.4 deleted them silently). Test assumption; the preview wording is a follow-up.

Adapter changes: paperless/queued.py (TaskWaiter) follows the named task until success, failure or revoked (poll 0.2 s, 60 s bound, then UpstreamTimeout naming the task). Wired into PaperlessBulkWriter.bulk_edit_documents, PaperlessDocumentWriter.merge_as_versions and PaperlessPdfWriter._run (which then looks for the file tasks with a fresh window; a failed task becomes the error 3.4 raised: not a PDF, wrong password, or could not apply the operation). An answer without task_id (3.4.2) is not waited for: behaviour unchanged. The version is detected by the shape of the answer, not by the version string. A user without view_paperlesstask (403), a task row that stays invisible for 3 s or an unreadable task list cannot be followed: the waiter gives a fixed 2 s grace (best effort, documented; follow-up PLMCP-48). get_config and update_config turn a 403 into a PermissionDenied with the READ_CONFIG hint.

Tests: wire-level adapter tests for both answer shapes (tests/adapter/test_queued.py, test_bulk, test_pdf, test_versions, test_admin); contract and e2e expectations branch on the release (contract.conftest.since, e2e.helpers.since) with the same assertions on 3.4.2 as before, none weakened. FakePaperless still models only 3.4.2 (documented): a 3.5 fake mode is PLMCP-47. Honest note on order: queued.py was written before its own unit tests; the wiring tests (bulk, pdf, versions) were run red before the wiring, and mutation testing then drove extra assertions (exact messages, injected clock and sleep).

Evidence: just lint, format-check, typecheck (basedpyright 0 warnings), imports, deps, secrets and lock-check green. Unit and adapter suite 4400+ passed. Live on 3.5.1: the whole suite via PAPERLESS_E2E_VERSION=3.5.1 just e2e-latest on a fresh stack: 629 passed, 6 skipped (AI), 0 failed. Live on the pinned 3.4.2 (port 8010): all touched modules (contract bulk, pdf, document_versions, admin, notes_and_trash; e2e bulk, pdf, versions, mail, taxonomy_edits): 152 passed. Mutation (mutmut) on paperless.queued, paperless.bulk, paperless.pdf, DocumentWriter.merge_as_versions and __init__, admin._read_config: no new survivors; the only survivors are the lowercase post method mutants already in mutation-equivalents.toml (bulk_edit_documents_21, merge_as_versions_20, pdf _run_17); their function_hash values were refreshed because the functions changed (the equivalence is unchanged: httpx2 upper-cases the method). The remaining timeouts are loops that the mutation makes endless.

Vendored docs proposal (not done, the pin is untouched): moving docs/vendor/paperless and the compose digest to 3.5.x is a pinned-version decision. The 3.5.1 schema differs from the vendored 3.4.2 one by task_id on the eight results, a note format field (plain or markdown) and the NoteCreateRequest rename, the run_workflow bulk method, workflow trigger type 5 Manual and extra workflow trigger filters (enabled, id, id__in, trigger_type). Tracked as PLMCP-47.

Possible upstream issue, not papered over: the login throttle (429 on /api/token/) hit a long-lived 3.5.1 stack after many test sessions; a fresh stack does not hit it, as in CI.
Follow-ups: PLMCP-47 (3.5 mode in the fake, vendored docs and pin decision, tag-deletion preview wording and a hint on that 403), PLMCP-48 (unobserved and slow queued tasks).

Controller: AC #2 is split. Adapter handling of both versions is done and wire-tested here; fake-based contract coverage of 3.5 moves to PLMCP-47, which already carries it. AC #3: e2e-latest against 3.5.1 is green (629 passed); the full pinned 3.4.2 suite is verified by the post-merge e2e-full run on main, after which the controller sets this task Done.

DoD #1 (tests first) unchecked on review: queued.py and the admin READ_CONFIG hint were written together with or before their first tests, which doc-2 step 4 does not allow; the wiring tests (bulk, pdf, versions) were red first. For the review round (unconfirmed outcome, PDF writer, neutral failure messages) tests were written first and run red before the code: test_queued (confirmed/Unconfirmed), services bulk_documents/versions/pdf unconfirmed tests, adapter pdf tests for the neutral failure messages and the unconfirmed StartedTasks.

Review round: (1) TaskWaiter.confirmed returns Unconfirmed(task_id, reason) instead of None for 403, never-visible row, unreadable list at the deadline and a task still running at the deadline; transient read errors are waited out. BulkWriter and merge_as_versions return it; DocumentBulkReport and VersionsMerged carry unconfirmed_task_id and a message that does not claim success (surface snapshot: exactly these two fields and one description changed, deliberately). (2) PdfWriter returns the bulk_update task id with problem=unconfirmed and a reason instead of raising; the lock stays over wait and lookup because a concurrent request would make file tasks with the same names. (3) failed queued edit_pdf and remove_password name no single cause. (5) the waiter reuses PaperlessTaskReader. Evidence: unit 4439 passed, cov-diff 100 %, lint/format/basedpyright/imports/deps/secrets green, live touched modules 152 passed on 3.4.2 and 152 on 3.5.1, mutation: no new survivors (equivalents re-hashed).

Controller, after merge: e2e-full on main (3.4.2) green in 3 shards. The post-merge e2e-latest dispatch on 3.5.1 (run 45034059493) failed 3 contract tests in test_bulk: a restricted user without view_paperlesstask gets Unconfirmed after the 2 s grace (intended, PLMCP-48), and the tests read the document as the admin before the queued task had run on the slower hosted runner. Test assumption, not an adapter bug: the tests now wait for that task as the admin (settled()) before asserting, with the assertions unchanged; on 3.4.2 the writer returns None and nothing waits. Verified locally: tests/contract/test_bulk.py live on 3.5.1, 42 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Paperless 3.5 queues every document operation (bulk_edit, delete, reprocess, rotate, merge, merge_as_versions, edit_pdf, remove_password) as a bulk_update task and answers a task_id; 31 of the 35 failures came from reading back, or looking for file tasks, before that task had run. New paperless/queued.py (TaskWaiter) follows the task; the bulk writer, the version merge and the PDF writer use it, and a failed task becomes the error 3.4 raised. 3.4.2 answers (no task_id) are unchanged. The other 4 failures are intentional 3.5 changes (config read needs a permission, blank notes rejected, fixed-length mail password mask, tag deletion refused when it would take others' child tags): the adapter gives a hint for the config 403 and the live tests branch on the release without weakening 3.4.2 assertions. Verified: lint, format, basedpyright, imports, deps, secrets, 100 % diff coverage, 4400+ unit/adapter tests; full just e2e-latest on 3.5.1: 629 passed, 6 skipped; touched live modules on pinned 3.4.2: 152 passed; mutation on the changed modules without new survivors (three existing 'post' equivalents re-hashed). Open: AC 2 (FakePaperless has no 3.5 mode: PLMCP-47) and AC 3 (the full pinned 3.4.2 suite was not re-run locally, only the touched modules; CI runs it on merge). Risks: users without view_paperlesstask are only given a 2 s grace (PLMCP-48); a task over 60 s raises a timeout though the change still applies.
<!-- SECTION:FINAL_SUMMARY:END -->
