---
id: PLMCP-80.1
title: 'Bulk changes a: choose documents by ids, filters or view, read_documents'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:32Z'
updated_date: '2026-11-24T14:34:43Z'
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

Review of f802555 (0 Critical, 3 Important, 9 Minor), all fixed, tests red first: I1 the domain test sets all 21 filters, contract cases go through parts() and each leaves out a seeded document; the 13 mutants of the review are killed. I2 one failing text is named in failed and the others go on; 429, a rejected credential and no connection end the texts. I3 a document that was not asked for refuses the call, one named twice counts once. M1 stated: 429 is told after the client's own retries (three tries, decision-70), proven for the three reads. M2 the notes check fails closed. M3 tags can be left out of an oversized row. M4 true and false are no ids, dates are YYYY-MM-DD, in the strict filters and the id arguments of read_documents only; search_documents hands over values FastMCP has already parsed, so it is unchanged. M5 view 0. M6 decision-69 and Selected.excluded_unknown. M7 'both named and excluded'. M8 fake refuses AND/OR without a condition. M9 text. Also: notices at most 5,000 characters (a hostile query made the answer 209,434 characters), also_matching on page 1 only, one ViewService.

Live on f802555 (CI run 45410532339, part 4/4): the six tests of tests/e2e/test_read_documents.py pass on 3.5.1, with 1,000 and 1,001 documents (16 s for the whole test), a deleted tag in a view (P10) and the restricted user. New: time of 1,000 matches by title, text and more-like.

Delta review of ab8d5a9 (3 Important, 4 Minor), all fixed, red first: N1 the indexed documents have a title and a date of their own. N2 excluded_absent (listed and absent: deleted, or hidden; noticed conditionally, since a hidden one does exclude) apart from excluded_unchecked (kind may not be listed, no notice). N3 two outages in a row (502, 503, 504, timeout) end the texts and go back into left; one alone stays per document, so that one text cannot block every call. N4 to N6 notices to the last character, own before query, failed without cut rows. N7 decision-69 says what the similarity measurement is.

Live on f802555 (CI 45410532339): all 27 cases of tests/contract/test_selection.py pass on 3.5.1: P1 (each field), P9 (rows hold notes also for a user without view_note, as decision-70 says), P10 (ids with filters, a deleted tag). The four failures of that run are tests of PLMCP-79.3 (test_pdf.py contract and E2E, search by file name).

Last review of ecb8e68, fixed red first: of two outages in a row the first stays in failed and only the second and the rest go to left (corrects N3 above); a 404 between two outages parts them; texts; one rule with an existing and a gone id is tested again.
<!-- SECTION:NOTES:END -->
