---
id: PLMCP-80.1
title: 'Bulk changes a: choose documents by ids, filters or view, read_documents'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:32Z'
updated_date: '2026-11-24T14:06:51Z'
labels:
  - bulk
dependencies:
  - PLMCP-79.3
parent_task_id: PLMCP-80
priority: medium
type: feature
ordinal: 92000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-80: nobody can read many documents in one call, and a set chosen by search cannot be shown or protected against changes. Design approved by the owner on 2026-11-24 (decisions 1 to 20 as recommended); file design-plmcp-80.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 read_documents reads up to 100 documents per call by ids, search filters or saved view, with selectable fields, content only on request and at most 20000 characters of text and 100000 of answer per call
- [ ] #2 At most 1000 documents may match; every match is named and a changed set between preview and confirm is refused
- [ ] #3 Contract, tool and E2E tests cover limits and a restricted user
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
1. Domain: SelectionFilters (the 21 filters of search_documents), Selection, projected row and report. 2. Port select_documents and read_rows: adapter, fake, adapter and contract tests (the probes P1, P6 reading part, P9 reading part, P10 as live contract tests). 3. services/selection.py (resolve, refuse, compare with given ids) and services/read_documents.py. 4. Tool read_documents in bulk, registry, texts, docs, E2E. Red first at every level.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approvals (2026-11-24): 'Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits.' Used here: 1A, 2a, 3a, 10a, 14 (guide limit), 15a, 16a, 17.

Built: domain/selection.py, services/selection.py (DocumentSelector), services/read_documents.py, DocumentReader.select_documents and read_rows, tool read_documents (bulk, read-only, off by default). search_documents goes through the same SelectionFilters; its schema is unchanged (snapshot: only insertions). decision-69 and decision-70 (new, supplement 4, 6, 15, 44, 53). OVERVIEW_GUIDE_LIMIT 2865 to 2882 (17 bytes). docs/tools.md, docs/api-coverage.md, README counts, configuration, doc-1 section 6 updated; query:fields of GET /api/documents/ is no longer open.

Deviations from the design: (1) read_documents also takes ids together with filters or a view (the same arguments as the other three tools): the only place where AC 2's changed set can be shown in 80a. (2) selected_ids (every match) stands in the answer on page 1 only. (3) Port read_rows(ids, fields) instead of (query, fields, page, page_size): the selection is resolved in a request of its own, so pages and exclude_ids work on ids. (4) An empty value of a filter is refused, for reading too. (5) notes: the right is checked with the notes endpoint of the first document, so it does not depend on P9. (6) tests/quality test of the derived coverage names read_documents beside search_documents for has_duplicates.

Probes for 80a, as contract and E2E tests (no scaffolding): P1 fields (tests/contract/test_selection.py, one test per field) and 1,000/1,001 rows live (tests/e2e/test_read_documents.py, bare database rows made with manage.py shell); P9 notes in the rows of a user without view_note; P10 ids with other filters, a deleted tag in a view. P2 to P5, P7, P8 and the writing part of P6 belong to 80b to 80d.

Evidence: domain, adapter, contract (fake), service and tool tests red before the code (missing module or method, then failing assertions), then green. 100 % of the new modules covered by the targeted run. Mutation by hand: 34 mutants of selection.py, read_documents.py and the adapter; 5 survived, tests added for the page boundary, the lookups and the size; left are the comma in the size (covered by the reserve for left) and room < 0 against <= 0 (exact fit). basedpyright, ruff, hygiene, import contracts green. The first E2E and live contract run is the hosted one.
<!-- SECTION:NOTES:END -->
