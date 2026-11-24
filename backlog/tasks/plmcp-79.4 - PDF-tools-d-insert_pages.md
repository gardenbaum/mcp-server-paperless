---
id: PLMCP-79.4
title: 'PDF tools d: insert_pages'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:17Z'
updated_date: '2026-11-24T14:45:51Z'
labels: []
dependencies:
  - PLMCP-79.1
  - PLMCP-79.2
parent_task_id: PLMCP-79
priority: medium
type: feature
ordinal: 89000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth part of PLMCP-79 (design approved by the owner on 2026-11-24 (all 14 decisions as recommended); file design-plmcp-79.md of the main agent, section 2.2, decision 1 B).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 insert_pages builds the file in the renderer's child process and uploads one new version or a new document; sources are untouched when a step fails
- [ ] #2 Limits, hostile inputs and what is lost are stated and tested
- [ ] #3 Rendering, service, tool, contract and E2E tests
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
1. Rendering: job kind assemble (worker, wire, runner), tests red first.
2. Service pdf_insert on the turn of PageService: checks, downloads, plan, dry run, re-read, one upload, outcome.
3. Tool insert_pages, annotations, guide limit, snapshot, docs, coverage tables.
4. Decision (by CLI) that extends decision-42 and reads decision-47 for this tool.
5. Contract and E2E tests, read from CI; hosted full E2E; notes.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built on main bdf8bf9 (79.1, 79.2), decision-68 (from the CLI), PR #135 (draft). Approval as in 79.1: 1 B, 5a, 7, 13 of the design ("Bitte alles wie von dir empfohlen umsetzen.", 2026-11-24).

Found while building (spike with the locked pypdfium2): importing next to a missing page of the target crashes PDFium (segfault), so every page must have a size first; an inserted page inherits /Rotate and /CropBox of the page tree of the target, so it gets entries of its own; a target whose /Count is too small would lose pages, so its count must be the one Paperless has; owner-password PDFs open without a password (target would stay encrypted, source would lose its restrictions), so both are refused. Other pages of the source are not taken along by links. Measured with 49 + 50 MiB on macOS: 0.8 s, 650 MB in the child, 203 MiB in the server besides the inputs.

Design choices: dry_run also downloads and puts the file together (only opening the files shows encryption and damage); new_document needs a view of the target only (Paperless asks for no more), new_version a change grant; metadata of the new document is read just before the upload; both children share 30 s, so the 150 s of the busy hint stay true; PAPERLESS_MCP_MAX_UPLOAD_BYTES does not apply; the product page count is checked in the renderer, so the port gives out checked files only.

Deliberate changes: OVERVIEW_GUIDE_LIMIT 2849 -> 2864 (15 bytes of the name; owner: at most 2880 for the three); surface snapshot: insert_pages added only; docs/tools.md and docs/api-coverage.md regenerated; README 95 tools; writing pdf tools with a preview 8; e2e_ci aliases and its pinned scope test; fake counts pages of uploads (also as PDFium writes them); pypdfium2 stubs grow; image_render_smoke puts two files together. No other limit raised. No read-only tool added, so tests/e2e/test_stdio.py is unchanged.

Deviation: rendering, service and tool tests were seen red by import or missing tool, some edge tests (annotations kept, imported twice, uncounted source) were added after the code; contract and E2E tests were written after the code.
Not run locally: full suite, E2E, live, mutation (owner rules).

Review of 9721107 (0 Critical, 3 Important, 5 Minor), tests red first (53 rendering, 11 service):
I1: marker pages in a saved and reopened throwaway copy prove every place; the result is reopened and every size compared; the second child (new job kind count) reads every size. All results accepted for wrong inner counts were also read alike by MuPDF and qpdf (36 cases, locally). 49 + 50 MiB: 0.76 s, 600 MB in the child (two parts are read apart now), 203 MiB in the server; 5000 into 5000 pages 5 s, 50 000 inserted pages end at 30 s.
I2, M1: the plan, tool text, decision-68 and SECURITY.md name what comes along, also invisible. Removing file-attachment annotations: about 15 lines, tried, works (the file does not come along); owner decides.
I3: the built file is dropped before the task is followed (weakref test); docs/container.md.
M2: UnexpectedAnswer (domain) for an unreadable success answer. M3: 429 is a refusal. M4: the import stubs are detected in the saved file. M5: rendering-windows runs tests/rendering with test_assemble (two files of 99 MiB together, memory, time) and was green on 9721107; nothing added, no limit.
Form values: the form of the source is loaded in memory, PDFium stores appearances; text, lines, check box, radio, combo, list with NeedAppearances true and false, shown by MuPDF, Poppler and Ghostscript; stored ones stay unless the file asks for new ones; no V8 or XFA in the build, scripts do not run (tested). Left as loss: not fillable, missing glyphs, this server draws widgets only in files with a form.
The restricted hint says that edit_pdf changes that document and needs its ownership. Surface snapshot: description of insert_pages only.

Delta review of a850b6f, tests red first (126 rendering, 4 service):
N1, N2: fields of inserted pages become one locked stamp annotation per page (rendering/fields.py): taken off in memory, flattened in a second copy, put on the imported page. Deviation from the proposal (form XObject as page content): tried first; a leftover clip, unbalanced q and unterminated string hid the values in PDFium, MuPDF, Poppler and Ghostscript (leaked text state such as 3 Tr would do the same), because PDFium appends without wrapping the old content. The annotation does not depend on it, is drawn by PDFium with and without forms, and pdftotext, MuPDF and Ghostscript extract the values as from the source (measured locally). What could still hide fields in the copy is refused as damaged. 49 + 50 MiB with a field 0.71 s, 600 MB; 2000 pages with a field each 1.9 s.
Left out: hidden, NoView and not-printed fields (as asked). Fields without the print flag are visible on a screen in the source: owner question.
n1: page labels in the plan and tool text. n2: reason size (over 10 000 000 points).
Surface snapshot: description of insert_pages only.

Coordinator: fields without the print flag are drawn, only hidden and NoView ones are left out (5 tests red first). Supersedes the owner question above.

Last delta review, 15 tests red first. F1: values, appearances and the way to the parent emptied in all fields of the source in memory before the import (a parent value referred to directly stays; stated and pinned by a test). F2: stamp as large as the fields; PDFium cannot order annotations, so beside other annotations one stamp per field (over 100: one). F3: reason fields with a hint. F4: streams decompressed in the tests.

Closing round on 7ab516e, tests red first (17, and 2 of shared fields, 1 of /Parent). S1/S2/G4: fields are counted in the written result; only if some came along everything is done again with every field of every page emptied, whatever the form type, in the source and in the copies of the pictures; an unreadable page then is fields. Covers /AA, /B, /A chains, /IRT, /Popup, private data, resources. Found on the way: page resources came along with the picture (values leaked), /Parent of annotations stays untranslated by PDFium (cut off), and a source closed before saving can crash PDFium (order kept). G1 to G6 done; measured again: 49 + 50 MiB with a field 0.75 s, 600 MB.
<!-- SECTION:NOTES:END -->
