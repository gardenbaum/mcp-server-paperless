---
id: PLMCP-46
title: Adapt to paperless-ngx 3.5.1 behaviour changes
status: To Do
assignee: []
created_date: '2026-11-22T12:11:00Z'
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
- [ ] #1 Each of the 35 failures is explained in the notes as an adapter bug, an intentional upstream change, or a test assumption
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
