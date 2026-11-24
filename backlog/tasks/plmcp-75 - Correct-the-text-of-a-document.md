---
id: PLMCP-75
title: Correct the text of a document
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:46Z'
updated_date: '2026-11-24T01:22:54Z'
labels: []
dependencies:
  - PLMCP-74
priority: high
type: feature
ordinal: 66000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Once a page can be read as an image, a wrong or missing OCR passage should be correctable, so search and the AI find it. The API takes content in PATCH /api/documents/{id}/ (per version with ?version=); no tool exposes it, and get_document_content reads the latest version only. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The text of any version can be read
- [x] #2 The content of a document or of one version can be replaced as a whole or by an exact passage, with a preview of the change and a confirmation
- [x] #3 A passage that is missing or not unique changes nothing and says so; if the text changed since the preview, nothing is written
- [x] #4 A decision supplements decision-12: content changes need confirm
- [ ] #5 The tool says that reprocessing overwrites the correction, and full-text search finds the new text (live test)
- [x] #6 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [ ] #7 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover whole and passage replacement, a version, a missing or ambiguous passage, a change in between and a restricted user
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
Approved design (2026-11-23) with the controller decisions: tool correct_document_text in versions, short preview plus full dry_run, documented race window.
1. Read: get_document_content with version_id and text_state (domain, port, adapter, fake, contract, service, tool).
2. Write port set_document_text: PATCH always with version and content in fields; adapter, fake, contract.
3. domain excerpt and services/text_edits.py: text_state check, unique passage, limits, preview through ConfirmationRequired.require with scope (version, text read, new text), dry_run with the full change in parts, re-read after the write.
4. Tool, confirmation tables, E2E, snapshot and budgets (declared).
5. Decision, docs, api coverage, doc-1.
Every step: failing test first.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-74: pages can be read as images with get_document_pages (any version). The core toolset has 20 of 20 tools since decision-42, so AC 6 (one free slot) no longer holds: the tool goes to an opt-in toolset, extends an existing tool, or needs a new budget decision. Reading the text of a version (GET /api/documents/{id}/ with version) is still open here.

Deviations and facts (2026-11-24):
- Decision 48. The scope of the confirmation binds version, text read and text to store (the design had none).
- dry_run returns the change in parts of 10,000 characters per side (offset, next_offset): one answer stays within a slice.
- The text is read again after the write, besides the answer of the PATCH; either mismatch is reported.
- PR E2E tier: test_text_edits is mapped only to its own files and services/text_edits.py. Mapping it to documents and versions too needs 1401 s against the budget of 3 x 450 s (test_the_real_cross_cutting_example_fits_the_budget_per_group); the main agent decided to keep it and not to change decision-30. e2e-full covers it after the merge. Its weight in MODULE_SECONDS (contract) is an estimate.
- Raised, declared: INSTRUCTIONS_LIMIT 1450 to 1468, OVERVIEW_GUIDE_LIMIT 2760 to 2802. The hostile-text test accepts JSON-escaped excerpts of at most 190 characters for this tool only.
- Not proven live here (contract and E2E run on GitHub): everything in section 11 of the design; P6 (oversized body), P9 for reading after a restore, P10 (audit log, workflows), P11 and P12 have no test.
- Mutation testing was not run (Linux only).
- just check on macOS: 1 failure, the known test_the_menu_driver_reports_a_failing_cli; 7080 passed; diff coverage 100 %.

Correction (2026-11-24): INSTRUCTIONS_LIMIT and OVERVIEW_GUIDE_LIMIT stay at 1450 and 2760 (measured 1423 and 2758). Shortened instead: 'HTTP and image: environment, restart ...' (28 bytes, its three exact-text tests follow or keep matching), summaries of versions ('text fixes') and bulk ('change many documents').
Hosted E2E on 2bcdbc6: two live contract tests failed because the fixture's grants (PATCH set_permissions) replace those of an earlier user on the same document, while the fake added them. Neither the product code nor fields/version was the cause. The fake now replaces them as well and failed the same two tests; the tests use one user after the other and now also assert that the refused users see the document.
<!-- SECTION:NOTES:END -->
