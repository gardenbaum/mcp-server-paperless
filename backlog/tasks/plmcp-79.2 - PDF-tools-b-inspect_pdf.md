---
id: PLMCP-79.2
title: 'PDF tools b: inspect_pdf'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:15Z'
updated_date: '2026-11-24T11:51:24Z'
labels: []
dependencies:
  - PLMCP-79.1
parent_task_id: PLMCP-79
priority: medium
type: feature
ordinal: 87000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-79 (design approved by the owner on 2026-11-24 (all 14 decisions as recommended); file design-plmcp-79.md of the main agent, section 2.1).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 inspect_pdf gives page count, sizes, rotation and contact sheets within the limits of decision-42
- [ ] #2 OVERVIEW_GUIDE_LIMIT rises for the three tools of PLMCP-79 at most to 2880 (owner, 2026-11-24)
- [ ] #3 Rendering, service, tool, contract and E2E tests
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
1. Rendering (red first): a second job kind "overview" in domain/rendering.py, worker.py, wire.py, runner.py: layout runs (pages, size in mm as shown, stored rotation, at most 100 runs) and contact sheets of 4 x 3 numbered tiles, at most 5 sheets, stop after 20 s. The server derives the page numbers itself from the specification; the child only sends counts and ranges-checked numbers.
2. Service: PageService.inspect shares the turn, download, limits and missing-file answers of show; only PDFs and archive PDFs.
3. Tool inspect_pdf in tools/pdf.py: media tool, read-only; surface snapshot, guide limit 2835 -> 2849, docs, counts.
4. Decision for MEDIA_TOOLS (design D3, inspect part; 61 holds the dry_run part).
5. Tool, transport, missing file, registry, sweep and E2E tests; docs/tools.md and api-coverage.md regenerated.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (copied from the design): "Presented to the owner as numbered decisions 1 to 14 with the recommendations above (1 B, 2 four pull requests with a Backlog task each, 3a incl. merge and remove_document_password, 4 no, 5a, 6a incl. delete_original(s), 7 limit 2,880, 8 dry_run, 9 contact sheets, 10 first option, 11 yes, 12 without finish_split, 13 skip, 14 PDFs only). Owner: 'Bitte alles wie von dir empfohlen umsetzen.' Excluded parameters (6) are thereby owner-approved exclusions of 2026-11-24."

Changed on purpose, with reasons:
- OVERVIEW_GUIDE_LIMIT 2835 -> 2849: the 14 bytes of the name inspect_pdf (owner: at most 2880 for the three tools). DEFINITION_ADDED_LIMIT unchanged (879).
- Surface snapshot (all, read-only): inspect_pdf added, no other entry changed. docs/tools.md and docs/api-coverage.md regenerated.
- MEDIA_TOOLS + inspect_pdf: decision-65 (number from the CLI; inspect part of design D3, 61 holds the dry_run part).
- test_every_writing_pdf_tool_has_a_preview counts writing tools only (inspect_pdf has nothing to preview); still 7.
- e2e_ci: a change of the pdf toolset also runs tests/e2e/test_pages.py, which holds the live tests of inspect_pdf; the pinned scope test says so.
- get_document_pages: source="archive" of a file it cannot show and without an archive now says that at once instead of sending the caller to source="original", which was refused then.
- README said 91 tools in one place: now 94 everywhere.

Design choices: layout covers the pages asked for (default all), also with the overview; a page that cannot be read is a run without size and an empty tile instead of failing the call; sheets share the 720 KiB (a lower quality first) so 60 pages are always shown if time allows; the 20 s stop is between tiles, the layout takes at most half of it, at least one page is always shown. Measured on macOS: 60 A4 scans of 300 dpi (120 MB, one picture repeated) 2.6 s, 360 MB.
Known limit: a UserUnit of the file is not applied to sizes (PDFium does not report it); in docs/configuration.md.
Not run locally: E2E, the full suite, the runner tests near 100 megapixels, mutation. The weight of test_pages.py (49 s) was not re-measured.

DoD 1 left open: rendering, service and tool tests were seen red (the service tests by running them against the old module); the contract and the E2E tests were written after the code and the E2E tests have not run yet.

Review fixes (0 Critical, 3 Important, 7 Minor), tests red first (rendering by import, then the number test against the mutant str(page + 1); service and tool tests before the change). I1: contract PDF has 24 to 30 characters of text, so 3.5.1 makes an archive; the E2E PDFs whose archive is read have no text. I2: each number strip is compared with one drawn in the test. I3: no tile is started that would end after 24 s if as slow as the slowest; the child announces each page, a timeout names it; one load per tile (no seam to count loads, so not tested by itself). Supersedes the earlier notes: with sheets the layout is of the pages on them (M5), with overview=false of all. M1: the UserUnit clause (PDFium has no call for it). M2, M3, M4, M6, M7 done; the rest of a list that does not fit 200 characters is named in the note. Surface snapshot: description of inspect_pdf only. The weight of test_pages.py is refreshed with the next hosted runs.

Correction: the service and tool tests of the review fixes were seen red afterwards, by running them against src of 6c41471.

M5 taken back (controller, 2026-11-24): the layout is of all pages asked for, also with sheets, as the owner approved in design 2.1; its cost on next_pages calls is stated in decision-65. I3b is now tested: overview takes the function that opens the PDF, and a counting document shows that each page is loaded once for its size and its tile (the layout keeps the at most 60 pages whose tiles follow). The 24 s bound counts the layout too (tested). This supersedes the notes above on both. Surface snapshot: description of inspect_pdf only.

Delta review (6 Minor and the layout): sheets first, then the layout of the remaining pages asked for until the 20 s, with the shapes the tiles read (a counting document shows one load per page). The child announces the page count and one mark per page, tiles and layout, and a mark when it is at none; the server names the page from the request and only for this job. Hint: ask for the page alone or leave it out; overview=false only where there was drawing. A real child with a PDF whose second page does not end names page 2; that test and the one of the time before the drawing kill the mutants without flush and without started. Tests seen red first (24 failed), except the one of the time before the drawing, which was shown by its mutant. Output bound of 1 MiB unchanged: 720 KiB, 200 000 marks and the head fit (tested).

Last review (d1 to d4): decision-65 corrected in two places; the timeout says overview=false only where a tile was drawn (a mark of its own for layout pages) and asks for the page alone only if others were asked for; announcements are taken off the start of the answer only (the test fails with sub); the test with a page that does not end gives the child 10 s. Red: by import error, the start-only test by its mutant.
<!-- SECTION:NOTES:END -->
