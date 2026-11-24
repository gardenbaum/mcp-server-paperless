---
id: PLMCP-79.1
title: 'PDF tools a: correct page checks, missing options, preview'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:51:14Z'
updated_date: '2026-11-24T08:59:46Z'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe module (P1 to P8, P11) on GitHub; facts into the notes.
2. T1: the adapter takes the version a request reads (domain PageSource), the fake gets a page count per version; contract tests both ways.
3. version_id, use_original_files, keep_metadata, archive_fallback through domain, port, adapter, fake, service, tools.
4. Merge omission check before the request, page count check after it.
5. plan and dry_run on every tool; confirmation scopes.
6. Trashing last by this server after every product was verified (split, delete_pages, edit_pdf, merge, remove_password); decision-60; coverage rows.
7. Probes become contract tests, probe module deleted; E2E; docs, snapshot.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Owner approval (2026-11-24)

Presented to the owner as numbered decisions 1 to 14 with the recommendations above (1 B, 2 four pull requests with a Backlog task each, 3a incl. merge and remove_document_password, 4 no, 5a, 6a incl. delete_original(s), 7 limit 2,880, 8 dry_run, 9 contact sheets, 10 first option, 11 yes, 12 without finish_split, 13 skip, 14 PDFs only). Owner: "Bitte alles wie von dir empfohlen umsetzen." Excluded parameters (6) are thereby owner-approved exclusions of 2026-11-24.
Note: PLMCP-101 raised DEFINITION_ADDED_LIMIT to 879; PLMCP-78 takes decisions 54/55, 101 takes 57; decisions of 79 start at 60.
<!-- SECTION:NOTES:END -->
