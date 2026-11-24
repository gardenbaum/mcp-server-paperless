---
id: PLMCP-80.2
title: 'Bulk changes b: bulk_edit_documents by filter and view, full scope and texts'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:33Z'
updated_date: '2026-11-24T18:03:55Z'
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
- [x] #1 bulk_edit_documents takes ids, search filters or a saved view and changes only documents that were shown
- [x] #2 Confirmation texts name all documents, up to 100, and bind the current values
- [x] #3 The coverage table quotes the owner-approved exclusions with their date
- [x] #4 Contract, tool and E2E tests cover partial failures and a changed set
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
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

Review of 162205a (0 Critical, 3 Important, 7 Minor), red first. I1 an unconfirmed change never says to go on; remaining counts what was not seen changed. I2 HTTP 429 of the task list ends the bulk edit at once (Unconfirmed.rate_limited, nothing read again, outcomes unknown); a parameter stop_on_limit of the waiter and of BulkWriter.bulk_edit_documents, off for the pdf tools, the version merge and resolve_duplicates, which need the result of the task. Correction: deviation (7) above is gone. The client's own retries of a read (three tries) stay. I3 new custom field values, all tags and fields and the parent tags that come along are in the texts. M1 decision-71 states 24,281 characters measured with hostile titles, with a test. M2 at most 100 mirrored documents are named. M6 the E2E test names Paperless's check. M7 row PLMCP-80.4. M3 to M5 (wording) reached the implementer without their text: the texts and docstrings were gone through (matched, remaining, also_matching, affected_count, the sentence after a lost answer, unknown outcomes, two docstrings that promised nothing half done). 15 more mutants, all killed.

Owner, 2026-11-24 ('bitte alles wie von dir empfohlen', relayed by the main agent): restricted users whose view excludes a hidden tag stay refused; ids plus a filter without a dialog stay as designed (current values not bound). Recorded in decision-71.

Checked: AC 1 and 2 (service, tool and dialog tests on both protocol eras), AC 3 (docs/api-coverage.toml, checked by tests/quality/test_api_coverage.py). Open: AC 4 until the E2E and contract tests have run live (run 45432664652 on 734904f was in progress); DoD 1 because the dialog, contract and E2E tests have no red run of their own (the mechanism existed or no stack was allowed locally); DoD 2 until CI is green (just check was not run locally, by instruction); DoD 3 until the PR text names the later snapshot changes; DoD 4 until merged (after the release 0.4.0 tag).

Live evidence for AC 4: full hosted E2E 45432664652 green on 734904f; repeated on the head with main merged. DoD 1 stays open: the dialog, contract and E2E tests were not seen red (adapter, service and tool tests were). DoD 2 and 4: the required CI checks and the merge of #137; just check was not run locally as a whole.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
bulk_edit_documents by ids, filters or saved view: only named documents change, confirmations bind current values, outcomes per document (decisions 71, 72). Reviews in two rounds.
<!-- SECTION:FINAL_SUMMARY:END -->
