---
id: PLMCP-79.3
title: 'PDF tools c: metadata per part, safe replacing, extract_pages'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:16Z'
updated_date: '2026-11-24T13:00:33Z'
labels: []
dependencies:
  - PLMCP-79.1
parent_task_id: PLMCP-79
priority: medium
type: feature
ordinal: 88000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-79 (design approved by the owner on 2026-11-24 (all 14 decisions as recommended); file design-plmcp-79.md of the main agent, sections 2.3, 2.4).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Split sets title, type, correspondent, date and tags per part; every part reports state, metadata and the next call
- [ ] #2 extract_pages keeps or removes the pages of the source; every failure row of the design is tested with its state
- [ ] #3 Nothing is trashed while a part is unfinished, failed, hidden, duplicate, stopped or without its metadata
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
1. Hidden products (owner ruling of 2026-11-24): PdfWriter.find_copies finds the documents of a request whose tasks a date custom field hides by file name; adapter, fake, contract (fake and 3.5.1), doubt rules.
2. PartMetadata resolved before the start, parts verified and patched one by one through DocumentEditor (services/pdf_parts.py); split_document part_metadata; parts[] and original in the result; replacing waits for metadata.
3. extract_pages: new document, verify, metadata, re-read, new version of the rest; every failure row of the design.
4. Plan, confirmation texts and scopes, decisions, annotations, budgets, coverage table, README, docs/tools.md, snapshots.
5. E2E and live contract tests, read from CI; draft PR.
<!-- SECTION:PLAN:END -->
