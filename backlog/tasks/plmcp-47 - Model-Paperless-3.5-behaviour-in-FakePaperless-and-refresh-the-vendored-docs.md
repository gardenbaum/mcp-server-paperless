---
id: PLMCP-47
title: Model Paperless 3.5 behaviour in FakePaperless and refresh the vendored docs
status: Done
assignee:
  - '@claude'
created_date: '2026-11-22T12:40:00Z'
updated_date: '2026-11-22T15:48:49Z'
labels: []
dependencies: []
priority: medium
type: chore
ordinal: 47000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
PLMCP-46 made the adapter handle paperless-ngx 3.5.1 and made the live tests version-aware, but FakePaperless still models only the pinned 3.4.2 (config readable by anyone, blank notes accepted, synchronous document operations). Proposal, needing a decision because it moves the pinned version: pin 3.5.x (justfile PAPERLESS_VERSION, tests/e2e/compose.yaml digest, just docs-sync-paperless to refresh docs/vendor/paperless and openapi.json, which also adds task_id to the document-operation results, the note format field and the manual workflow trigger type), then drop the 3.4 branches or keep both behind the fake's release. Also: the tag-deletion preview text and the delete_taxonomy_item description say child tags of other users are deleted with the tag, which 3.5 refuses (403); reword to cover both and give that refusal a hint.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A decision records the new pin (or keeps 3.4.2) and why
- [x] #2 FakePaperless can act as 3.4.2 and 3.5.x for the config read, note validation and mail password mask, with contract tests for both
- [x] #3 The tag deletion preview and tool description are true on 3.4.2 and 3.5; the refusal on 3.5 carries a hint; snapshot changes are stated
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Decision: keep 3.4.2 as the minimum pin, latest 3.x covered weekly (AC1).
2. FakeState.release (default 3.4.2, 3.5.1 for 3.5 behaviour): config read needs view_applicationconfiguration, blank notes rejected, tag deletion refused when it would take other users child tags (single and bulk); mail password mask is never returned by any port so it cannot be modelled; queued document operations not modelled unless clean. Tests first: tests/fakes/test_paperless.py and contract tests on a new fake-3.5 backend (a parametrize mark for the version-branching tests).
3. Adapter hint on the tag-delete 403; reword the delete preview (taxonomy_edits, bulk_taxonomy), delete_taxonomy_item and bulk tag edit descriptions, DeletedTag description; update services/tool/e2e tests; regenerate the surface snapshot deliberately.
4. just check, live on 3.4.2 and 3.5.1, mutation on changed src modules.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Known and accepted until this task is done: on 3.5 the confirm preview of delete_taxonomy_item (and of the bulk tag edit) says child tags of other users are deleted with the tag, but Paperless 3.5 refuses that deletion (403). tests/e2e/test_taxonomy_edits.py asserts the preview text knowingly.

Test-first record: written and run red before code: fake unit tests (tests/fakes/test_paperless.py: release, config read, blank notes, tag deletion single+bulk), contract tests on a new fake-3.5 backend (BOTH_RELEASES mark: admin config read, notes, tag deletion single and bulk), adapter test for the tag-delete 403 hint, updated preview text in services and e2e tests. Mail password mask: no port returns the mask (the adapter drops it before a row is built), so the fake has nothing to model; it stays a live-only e2e assertion.

Implemented. decision-29 (accepted, body written in the file) keeps 3.4.2 as the minimum pin; the vendored docs/openapi stay at 3.4.2, so nothing under docs/vendor, the justfile or the compose digest changed.
FakeState.release (default 3.4.2; 3.5.1 for 3.5): config read needs view_applicationconfiguration (PermissionDenied with the READ_CONFIG hint), blank notes rejected (400 blank), tag deletion refused when a tag below it is not the user's (non-superuser; owner or unowned; single: 403 with TAG_DELETE_HINT, bulk: the objects_refused 403), paperless_version follows the release. Not modelled: mail password mask (no port returns it or its mask; live e2e test_mail only) and queued document operations/task_id (the adapter wire tests of PLMCP-46 cover both answer shapes; a fake with deferred application would change every bulk/PDF contract test).
Contract: BOTH_RELEASES mark (fake, fake-3.5, live) on the version-branching tests (admin config read, notes, new single and bulk tag deletion tests); since(backend, ...) already works for the fake through expected_version, which is now the fake's release.
Adapter: a refused tag DELETE carries TAG_DELETE_HINT; other kinds unchanged. Texts: the preview and message of delete_taxonomy_item and the bulk tag delete, the two tool descriptions and the DeletedTag.also_deleted description now say 3.4 deletes other users' child tags and 3.5 refuses. Surface snapshot regenerated deliberately: only those descriptions (delete_taxonomy_item and the bulk taxonomy edit in the all profile, the also_deleted field in both); no tool added, default profile budget untouched.
Evidence: just check green (4455 passed, diff coverage 100 %). Live, touched modules (contract test_admin, test_notes_and_trash, test_taxonomy_edits, test_bulk, e2e test_taxonomy_edits): 205 passed on the pinned 3.4.2 and 205 passed on 3.5.1 (the e2e test now asserts the new preview text and the 3.5 hint). Mutation: paperless.taxonomy, services.bulk_taxonomy and services.taxonomy_edits: no new survivors; the existing delete_taxonomy_item lowercase-method equivalent got its function_hash refreshed.
Honest order note: tests were written and run red first at fake, adapter, service and contract level (the e2e assertion was edited together with them but ran only after the code existed, live). The decision body and the tag-deletion rule were checked against the 3.5.1 and 3.4.2 container sources (TagViewSet.perform_destroy, BulkEditObjectsView) before writing the fake.

Controller: reviewed the diff and decision-29; just check green. AC #2 accepted with the mail password mask left to the live tests: no port returns the mask, so the fake has nothing observable to model (recorded in decision-29). DoD #4 checked on this PR's merge.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
decision-29 keeps 3.4.2 as the minimum pin and has the latest 3.x covered weekly; FakePaperless now has a release setting (default 3.4.2, 3.5.1 for 3.5) that models the config read permission, blank-note rejection and the tag-deletion refusal (single and bulk). The version-branching contract tests run on the fake in both modes and live. A refused tag deletion carries a hint, and the delete preview, tool descriptions and field description are true on 3.4.2 and 3.5 (surface snapshot regenerated deliberately: those descriptions only). Not modelled: the mail password mask (no port exposes it) and queued document operations. Verified: just check, live on 3.4.2 and 3.5.1 (205 passed each), mutation with no new survivors. DoD 1 stays unchecked: the e2e assertion was not run red before the code (live-only).
<!-- SECTION:FINAL_SUMMARY:END -->
