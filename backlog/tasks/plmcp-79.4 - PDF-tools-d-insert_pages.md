---
id: PLMCP-79.4
title: 'PDF tools d: insert_pages'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:17Z'
updated_date: '2026-11-24T13:24:27Z'
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
