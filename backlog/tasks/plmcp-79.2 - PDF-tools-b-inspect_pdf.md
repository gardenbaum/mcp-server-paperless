---
id: PLMCP-79.2
title: 'PDF tools b: inspect_pdf'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:15Z'
updated_date: '2026-11-24T11:06:35Z'
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
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
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
