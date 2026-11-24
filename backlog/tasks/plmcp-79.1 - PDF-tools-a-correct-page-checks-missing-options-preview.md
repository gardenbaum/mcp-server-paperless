---
id: PLMCP-79.1
title: 'PDF tools a: correct page checks, missing options, preview'
status: To Do
assignee: []
created_date: '2026-11-24T08:51:14Z'
labels: []
dependencies: []
parent_task_id: PLMCP-79
priority: medium
type: feature
ordinal: 86000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First of four parts of PLMCP-79 (design approved by the owner on 2026-11-24 (all 14 decisions as recommended); file design-plmcp-79.md of the main agent, section 9). Fix T1 (pages are checked against the first file), the archive serial number text of remove_document_password, version_id, use_original_files, keep_metadata, archive_fallback, merge omission check, dry_run and plan on every writing tool; the server trashes originals last (merge, remove_document_password).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A document whose newest version is longer than its first file can be edited on every page, shown on 3.5.1
- [ ] #2 Every writing pdf tool has dry_run and a plan; confirmations bind what the design lists
- [ ] #3 The options of the design are sent; the owner-approved exclusions are in the coverage table with their date
- [ ] #4 No original is trashed before every product was verified, also for merge and password removal
- [ ] #5 Probes of the design became contract tests; fake, 3.5.1, tool and E2E tests cover it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
