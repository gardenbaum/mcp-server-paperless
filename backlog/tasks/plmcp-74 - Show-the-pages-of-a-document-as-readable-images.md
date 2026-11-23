---
id: PLMCP-74
title: Show the pages of a document as readable images
status: In Progress
assignee: []
created_date: '2026-11-23T12:14:45Z'
updated_date: '2026-11-23T19:37:40Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 63000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
get_document_thumbnail shows only a small image of page 1, and download_document attaches at most 512 KiB by default, while the owner's scans are often over 1 MB. An unreadable OCR spot (a signature date) could not be checked. Files as such are PLMCP-76; this task is about images a model can read. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A tool returns any page or page range as images a model can read, from the original, the archive file and any version
- [ ] #2 Resolution is selectable and a region of a page can be enlarged, so small print is readable
- [ ] #3 It works for PDFs and for image originals (JPEG, PNG, TIFF, WebP); text, e-mail and Office originals are shown through their archive PDF or refused with a reason; for encrypted or broken files it says why not
- [ ] #4 Large documents stay within stated byte, page, memory and time limits without changing the server's configuration; pages that do not fit are named with how to get them
- [ ] #5 The renderer is chosen in a decision (licence, size, security of parsing untrusted files, arm64, image scan) and runs within the container's restrictions (read-only file system, non-root)
- [ ] #6 Permissions are those of the token user; stdio and HTTP both work
- [x] #7 The tool is added to MEDIA_TOOLS (decision-11) with a reason in the task notes
- [x] #8 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [ ] #9 Contract tests on the fake and Paperless 3.4.2, tool tests and E2E cover every source type, page ranges, regions, each limit and broken or encrypted files; docs and tool reference are updated
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
1. version_id through DocumentReader.get_document_file and get_document_metadata (fake, adapter, contract tests).
2. Package rendering/: worker.render for PDFs (page, size, region, forms, errors), then images (JPEG, PNG, TIFF, WebP), then byte budget and next pages.
3. rendering/runner: one child process per call, no environment, no shared stdio, bounded time, memory, output; cancellation kills it; hardening of child and parent.
4. services/pages: version and source choice, limits, texts, one render at a time.
5. Tool get_document_pages (core, MEDIA_TOOLS), registry, snapshots, stdio and HTTP transport tests.
6. Smoke scripts, windows-latest job, weekly pypdfium2 alert, compose overlay with Tika and Gotenberg, E2E tests (run in phase 2).
7. Decision, docs, coverage table, notes of PLMCP-75, 76, 79, 83; gates.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Phase 1 (2026-11-23): everything except Docker and E2E runs.
MEDIA_TOOLS (AC 7): get_document_pages is listed because its result is images; a structured result next to them would make clients that prefer it drop the images (decision-11).
Limits with measurements (macOS arm64, this implementation, child peak): 60 pages at 300 dpi (150 MiB PDF) one page 0.45 s and 267 MB, region 0.46 s, five pages asked at 2576 px 0.95 s and 303 MB; JPEG 100 MP 0.7 s and 114 MB; PNG 99 MP RGB 1.4 s and 519 MB, RGBA 1.3 s and 917 MB, palette with transparency 1008 MB; 16 bit grey 292 MB; pure noise at 2576 px is reduced to 1086 px in 2.2 s. 720 KiB in one or five images pass a real uvicorn server in both client modes.
Watchdog: fails closed on any error (found by a review of the first version; 10 tests red first).
Deviations from the design: page_count and messages come from this process, the child sends no text; the region is at least 1 % of a side instead of 1 pixel; the download has its own limit of 120 s; resident memory on Linux is read from /proc/self/statm because ru_maxrss hands down the parent's peak; RLIMIT_NOFILE=0 only without system fonts, so that glyphs of unembedded fonts do not change.
mutmut does not cover rendering/ (its packages are services/ and paperless/): the logic was mutated by hand.
Gate changes: new CI job rendering-windows (not required), new job e2e-office in e2e-full.yml, import contract 'Only rendering imports pypdfium2 and PIL' and rendering in the layer contracts, stubs in typings/ with ruff exceptions for upstream names. No gate is weaker.
Snapshots: test_surface (new tool; get_document_thumbnail names it), test_container (image.yml path filter).

Hand mutations of rendering/, services/pages.py, page_ranges, tool, adapter, cli: 45, of which 2 survived at first (answer without images; Pillow plugins loaded before the child may open nothing); both have tests now, seen red. Gates on macOS: test suite green but for the known macOS-only test_the_menu_driver_reports_a_failing_cli, diff coverage 100 %. Open for phase 2: Docker image, Linux-only tests, E2E, Office overlay run time, shard weight of tests/e2e/test_pages.py, Windows job.
<!-- SECTION:NOTES:END -->
