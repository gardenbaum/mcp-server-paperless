---
id: PLMCP-47
title: Model Paperless 3.5 behaviour in FakePaperless and refresh the vendored docs
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-22T12:40:00Z'
updated_date: '2026-11-22T15:26:32Z'
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
- [ ] #1 A decision records the new pin (or keeps 3.4.2) and why
- [ ] #2 FakePaperless can act as 3.4.2 and 3.5.x for the config read, note validation and mail password mask, with contract tests for both
- [ ] #3 The tag deletion preview and tool description are true on 3.4.2 and 3.5; the refusal on 3.5 carries a hint; snapshot changes are stated
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
1. Decision: keep 3.4.2 as the minimum pin, latest 3.x covered weekly (AC1).
2. FakeState.release (default 3.4.2, 3.5.1 for 3.5 behaviour): config read needs view_applicationconfiguration, blank notes rejected, tag deletion refused when it would take other users child tags (single and bulk); mail password mask is never returned by any port so it cannot be modelled; queued document operations not modelled unless clean. Tests first: tests/fakes/test_paperless.py and contract tests on a new fake-3.5 backend (a parametrize mark for the version-branching tests).
3. Adapter hint on the tag-delete 403; reword the delete preview (taxonomy_edits, bulk_taxonomy), delete_taxonomy_item and bulk tag edit descriptions, DeletedTag description; update services/tool/e2e tests; regenerate the surface snapshot deliberately.
4. just check, live on 3.4.2 and 3.5.1, mutation on changed src modules.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Known and accepted until this task is done: on 3.5 the confirm preview of delete_taxonomy_item (and of the bulk tag edit) says child tags of other users are deleted with the tag, but Paperless 3.5 refuses that deletion (403). tests/e2e/test_taxonomy_edits.py asserts the preview text knowingly.

Test-first record: written and run red before code: fake unit tests (tests/fakes/test_paperless.py: release, config read, blank notes, tag deletion single+bulk), contract tests on a new fake-3.5 backend (BOTH_RELEASES mark: admin config read, notes, tag deletion single and bulk), adapter test for the tag-delete 403 hint, updated preview text in services and e2e tests. Mail password mask: no port returns the mask (the adapter drops it before a row is built), so the fake has nothing to model; it stays a live-only e2e assertion.
<!-- SECTION:NOTES:END -->
