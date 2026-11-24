---
id: PLMCP-80.2
title: 'Bulk changes b: bulk_edit_documents by filter and view, full scope and texts'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:33Z'
updated_date: '2026-11-24T15:37:36Z'
labels:
  - bulk
dependencies:
  - PLMCP-80.1
parent_task_id: PLMCP-80
priority: medium
type: feature
ordinal: 93000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-80: bulk_edit_documents takes ids only and its confirmation names three documents and a count. Design approved by the owner on 2026-11-24 (decisions 1 to 20 as recommended); file design-plmcp-80.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 bulk_edit_documents takes ids, search filters or a saved view and changes only documents that were shown
- [ ] #2 Confirmation texts name all documents, up to 100, and bind the current values
- [ ] #3 The coverage table quotes the owner-approved exclusions with their date
- [ ] #4 Contract, tool and E2E tests cover partial failures and a changed set
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
1. Probes as contract/E2E tests: P2 (Paperless's own filters, read-only through selection_data), P5 (delete and reprocess endpoints). 2. Domain: report fields, TaskFailed. 3. Adapter: documents/delete/ and documents/reprocess/. 4. Services: operation builders move to bulk_operations.py; BulkDocumentEditor takes a Selection, names every document, binds the current values, never acts by a filter alone, reads again after a failed, lost or unconfirmed change. 5. Tool arguments, texts, snapshots, docs. 6. Decisions, coverage exclusions, E2E. Red first at every level.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approvals (2026-11-24): 'Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits.' Used here: 1A, 2a, 3a, 4a, 5, 12, 13a (the omissions of 2, 12 and 13 are quoted with their date in docs/api-coverage.toml, source decision-72).

Built: BulkDocumentEditor takes a Selection; operation builders moved unchanged to services/bulk_operations.py (plus the current values each touches); report fields matched, selected_ids, remaining, also_matching, outcomes; TaskFailed (a Validation) tells a failed task from a refusal; trash and reprocess through their own endpoints (resolve_duplicates too). decision-71 and decision-72 (new, supersede nothing). Guide limit unchanged.

Decided here (decision-71): missing ids refuse as before; excluded_absent refuses a change and its preview (fail closed; the way on is ids without the filter); excluded_unchecked does not refuse, the confirmation says it could not be checked.

Deviations: (1) selected_ids holds the documents this call changes (the next call's document_ids), not every match; matched, unchanged_count and remaining count the rest. (2) The refusal of a filter cannot be told apart by confirm, so a client with dialogs is asked even with confirm=true. (3) Mirrored documents are no scope of their own: they follow from the bound values and the change, and are named in the text. (4) A selected document that only mirroring changes is named now (a test pinned the opposite). (5) 'remaining' is not in the dialog text: more matches would ask again without another change. (6) With ids and a filter (no dialog) current values are not bound: the server keeps nothing between calls (decision-71, as the design's table). (7) The wait for a queued task still polls through a 429 of the task list (existing, shared with the pdf tools); after that the documents are read once.

Probes: P2 is tests/e2e/test_bulk.py::test_paperless_takes_a_filter_it_does_not_understand_as_none (selection_data, only read). P5 (delete and reprocess endpoints: unknown, repeated, hidden, trashed ids, rights) are contract tests; P5's set_permissions part is source reading only (the form is excluded). P3 is not forced live: modify_custom_fields writes one value to all documents, so a value the database refuses fails at the first one; a failure half way is proven on the fake. P6: the time of 100 documents is printed by the E2E test.

Evidence: adapter, service and tool tests red first (missing endpoint, class or argument, then assertions); the dialog tests pass on the existing confirmation mechanism and were checked by mutants instead. 36 mutants of bulk_documents.py, bulk_operations.py and selection.py by hand: 8 survived, 7 got tests, 1 was redundant code and was removed. Every line of the changed production modules covered by targeted runs. ruff, hygiene, basedpyright, import contracts green. Not run locally: full suite, E2E, full mutation (by instruction).
<!-- SECTION:NOTES:END -->
