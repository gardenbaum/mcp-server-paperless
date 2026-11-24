---
id: decision-65
title: inspect_pdf reports pages and contact sheets as a media tool, from PDFs only
date: '2026-11-24T11:13:43Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Paperless tells of the pages of a document only how many there are. To decide where to split or cut, a caller needs sizes, rotation and a look at all pages (AC 1 of PLMCP-79). `get_document_pages` shows five pages a call.

The design was put to the owner with recommendations: contact sheets (decision 9 of the design), PDFs only (14), the guide limit of the overview up to 2880 bytes for the three new names (7). On 2026-11-24 the owner approved: "Bitte alles wie von dir empfohlen umsetzen." (please do everything as you recommend) (PLMCP-79). The rest was decided within the design.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `inspect_pdf` belongs to `pdf`, is read-only, and joins `MEDIA_TOOLS` (decision-11): its payload is images. With `overview=false` it returns the report alone, still as a text block, because one tool has one form. In read-only mode it is the only tool of `pdf`.
- It reads PDFs and archive PDFs. Any other original is read through its archive, or refused with the tool that can show it.
- It uses the child process of decision-42 with a second job kind; every limit of that decision holds unchanged and the turn is shared with `get_document_pages`: 5 images and 720 KiB a call, 100 MiB, 120 s, 1 GiB, 30 s, one document at a time.
- **Sheets.** 4 x 3 tiles, at most 1568 pixels on the long side, each page whole above its number; 5 sheets, so 60 pages a call. The sheets share the bytes of the call: one that does not fit gets a lower quality and then fewer pixels, and is marked `reduced`, so that no page is left out for its bytes.
- **Time.** After 20 s no further tile is drawn and the rest is named; nor is one started that, as slow as the slowest so far, would end after 24 s (counted from the start of the program of the child). The other 6 s are for writing the sheets, starting the process and the pipe. One page is always tried. The child marks each page before it loads it, on the sheets and in the layout, and marks when it is at none; one that takes the call over its 30 s ends it with an error that names the page and says to ask for it alone or to leave it out. The sheets are lost then, as today: a page slower than all before it cannot be known in advance.
- **Layout.** Runs of pages with the same size as shown (millimetres, one decimal, without UserUnit) and stored rotation. It is of all pages asked for, also with sheets (design 2.1), at most 100 runs a call, and goes on after the sheets until the 20 s, with the shapes read for the tiles, so no page is loaded twice in a call. A call with `next_pages` therefore gives the layout of the remaining pages again, which costs about 10 microseconds a page (measured, 20 000 pages). A page that cannot be read (missing, damaged, not between 0.1 mm and 10 km) is a run without size and an empty tile, and does not fail the call.
- **The rest** is a specification of at most the 200 characters the tools take; what does not fit is named in the note.
- **The answer stays untrusted.** The child sends counts; the server names the pages from the specification it was asked with. Numbers are range-checked, a mark names only a page that was asked for and only for this job, and there is no text in it.
- Supplements decision-11 and decision-42. That every writing pdf tool has `dry_run` is decision-61.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The overview of the server names one more tool (14 bytes of the guide).
- Sizes are PDFium's: a `UserUnit` in the file is not applied.
- A tile is about 250 by 350 pixels for A4: enough to tell pages apart, not to read them. The report points to `get_document_pages`.
- Every call downloads the file again.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/rendering/test_overview.py`: layout, sheets, limits, hostile answers, the child.
- `tests/services/test_inspect.py`: which file, refusals, missing files, the shared turn. `tests/tools/test_inspect.py` (marker decision-65) and `tests/tools/test_pages_transport.py`: blocks, arguments, the event limit in both modes, stdio.
- `tests/tools/test_registry.py`: MEDIA_TOOLS. `tests/tools/test_pdf.py`: read-only mode. `tests/tools/test_toolset_budget.py`: the guide limit.
- `tests/e2e/test_pages.py`: on Paperless 3.5.1.
