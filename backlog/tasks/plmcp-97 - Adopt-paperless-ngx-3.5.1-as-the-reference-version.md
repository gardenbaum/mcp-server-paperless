---
id: PLMCP-97
title: Adopt paperless-ngx 3.5.1 as the reference version
status: In Progress
assignee: []
created_date: '2026-11-23T18:13:36Z'
updated_date: '2026-11-23T19:17:05Z'
labels: []
dependencies: []
references:
  - https://github.com/gardenbaum/mcp-server-paperless/issues/103
priority: high
type: chore
ordinal: 74000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The weekly drift workflow reports that the vendored reference (3.4.2) differs from the latest 3.x release, 3.5.1 (issue #103: api.md, openapi.json, usage.md). The feedback tasks after it should build on the current API, and the coverage guard (PLMCP-91) shows what 3.5.1 adds. The controller put it before PLMCP-74 under the owner's delegation of 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The vendored reference, the E2E stack and every mention of the reference version are 3.5.1, with the image pinned by digest, and the drift check passes
- [x] #2 What changed between 3.4.2 and 3.5.1 is listed in the task notes from the release notes and the schema diff, with its effect on every tool
- [x] #3 Tools keep working on 3.4.2 as well, or a change of the supported versions is put to the owner before the merge
- [x] #4 Every new or changed endpoint, field and value of 3.5.1 is in the coverage table under a task that includes it, and the task text is extended where none does
- [x] #5 A plumbing row cannot hide an unsent field or value below it (left over from the PLMCP-91 review)
- [x] #6 The full E2E suite is green on 3.5.1
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Bump PAPERLESS_VERSION, run just docs-sync-paperless, pin the 3.5.1 image digest in tests/e2e/compose.yaml, EXPECTED_VERSION in the E2E helpers.
2. List the differences from the release notes (3.5.0, 3.5.1) and from the schema diff; run the unit, contract and E2E tests and fix what 3.5.1 breaks, test first and so that 3.4.2 keeps working.
3. Put every new field and value into the coverage table under a task whose text names it (extend PLMCP-80, 83, 87); make a plumbing row name its unsent values (AC 5), test first.
4. Update every statement of the reference version (docs, tests, tasks, design docs) but leave history alone.
5. Run the touched contract and E2E modules on 3.4.2 and 3.5.1, the whole E2E suite on 3.5.1, and the gates.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Reference bumped from 3.4.2 to 3.5.1 (vendored docs and schema via just docs-sync-paperless, image ghcr.io/paperless-ngx/paperless-ngx:3.5.1 pinned by digest sha256:8508853b8a0f8394074260957d0be186c8c64f346973b24a7583de4580f306b9 in tests/e2e/compose.yaml, PAPERLESS_VERSION in the justfile, EXPECTED_VERSION in tests/e2e/helpers.py).

Releases between 3.4.2 and 3.5.1: only 3.5.0 and 3.5.1 exist (no 3.4.3 or 3.4.4; checked in the GitHub releases and tags).

Differences, from the release notes (treated as data) and the schema diff (93 paths unchanged; 208 -> 209 schemas), with the effect on the tools:
1. Every document operation is queued and answers a task_id (3.5.0 "move all bulk operations to async hand-off", "make bulk metadata operations run async"). Schema: task_id (required) on the results of bulk_edit, delete, edit_pdf, merge, merge_as_versions, remove_password, reprocess and rotate. Effect: none new, PLMCP-46 follows the task on both releases; 3.4.2 answers only "OK". The schema guard accepts both shapes.
2. Notes render as markdown (3.5.0). Schema: format (plain, markdown; NoteFormatEnum) on the note read and on the create request (NoteCreateRequestRequest renamed NoteCreateRequest). Effect: the tools send no format (plain is the default) and do not show it. Open under PLMCP-83.
3. Manual workflow trigger (3.5.0). Schema: trigger type 5 (manual), bulk_edit method run_workflow. Effect: reading a workflow with a manual trigger already worked as "unknown type 5"; it now reads as "manual". Replacing the triggers of such a workflow would have deleted the manual trigger without a word (decision-21 treats types this server cannot write that way for actions): fixed here, test first. Writing a manual trigger is refused (3.4.2 refuses type 5, and it is a feature). run_workflow open under PLMCP-80, manual trigger writing under PLMCP-87.
4. from_webui (bool, default false) on the delete, reprocess and merge_as_versions requests. Effect: none, no tool sends it. Open under PLMCP-83.
5. GET /api/workflows/ filters enabled, id, id__in, trigger_type. Effect: none, list_workflows reads all pages. Open under PLMCP-87.
6. Behaviour changes already handled by PLMCP-46 and decision-29, now live on the reference: configuration read needs view_applicationconfiguration, blank notes rejected, constant mail password mask, tag deletion refused when it would take tags of other users ("check descendant tag permissions on delete").
7. Notes without a field in the schema: tags become an adjacency list (django-treenode removed; performance), no API change seen. Allauth 65.21.1, session clearing, OCR argument blacklist, PostgreSQL regex for scheduled workflow filename filters: no tool effect. PAPERLESS_ALLOWED_HOSTS: some deployments must adjust it (note of 3.5.0); upstream gives no more detail, and the E2E stack (reached as 127.0.0.1) is unaffected. 3.5.1: AI chat answers stream under ASGI (PR 14803 wraps the generator; the body format is unchanged as far as its diff shows) and a fix for the audit log setting in tasks. Effect on the chat tool is not verified: it needs an LLM backend (just e2e-ai).

Fixes made (test first, red then green; 3.4.2 unchanged): a manual trigger reads as "manual"; create and update refuse to write one; replacing triggers refuses to delete a stored manual trigger (WorkflowService, tools description, fake models type 5 only from 3.5). The schema guard accepts a queued answer with or without task_id (3.4.2 shape). The surface snapshot changed deliberately: one sentence about manual triggers in the workflows reference text (profile all only).
AC 5: unsent values below a plumbing field are a problem unless the plumbing row names them; the real table now names full_perms=false (5 rows) and imap_security=2, imap_security=3, is_token=true (mail process).
Coverage counts: endpoints 163 (105 covered, 58 open) unchanged; open-field rows 71 -> 75, open field keys 301 -> 311 (notes body:format, bulk_edit method run_workflow, from_webui x2, workflow filters x4, trigger type 5 x2); plumbing keys 22 -> 30.
Tasks: descriptions of PLMCP-80, 83, 87 extended; acceptance criteria of the To Do tasks that said "Paperless 3.4.2" or "the 3.4 schema" now say 3.5.1 and "the vendored 3.5.1 schema".
Evidence: unit suite 5494 passed (one known macOS failure, test_the_menu_driver_reports_a_failing_cli), diff coverage 100 %; live contract suite on 3.5.1 498 passed, 5 skipped (AI); touched and 3.5-sensitive contract and E2E modules (workflows, pdf, bulk, versions, notes_and_trash, taxonomy_edits, admin, stdio, stack) on 3.4.2: 289 passed; the whole E2E suite on fresh 3.5.1: 652 passed, 6 skipped (AI, needs a backend); just drift-check: no drift. Hand mutations of the new code in a scratch copy were all killed.
Open for the owner: decision-29 says the pin stays 3.4.2 and moves only by a superseding decision; this task moves it. Not verified: AI chat on 3.5.1 (needs an LLM backend), the weekly mutation gate (the changed functions have no equivalents entry).
<!-- SECTION:NOTES:END -->
