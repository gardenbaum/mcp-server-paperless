---
id: PLMCP-83.3
title: 'Rest of the API c: note format, thumbnail version, search and bundle filters'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T14:42:18Z'
updated_date: '2026-11-25T17:48:55Z'
labels: []
dependencies:
  - PLMCP-83.2
parent_task_id: PLMCP-83
type: feature
ordinal: 109000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-83: fields of covered operations that no tool sends. Design approved by the owner on 2026-11-24 with all recommendations ('Alle wie empfohlen', all as recommended); file design-plmcp-83.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 add_note and add_notes take the format plain or markdown, notes read back show it, and the same text in another format is another note
- [x] #2 get_document_thumbnail takes a version
- [x] #3 search_documents filters by modified from and to, archive serial number from and to and file name; list_share_bundles by status and document
- [x] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E
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
1. Tests first per level. 2. Note format (add_note, add_notes, notes read back); thumbnail version through listed versions; five filters in DocumentQuery and SelectionFilters (so in every choice by filter); status and document of list_share_bundles. 3. Coverage rows, docs, snapshots.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first at every level (94 tests of 17 modules), then green; 34 hand mutants: one equivalent (the format stands in the confirmation text too), one in the tool killed by a new test. decision-102 supplements decision-73. The five filters are in search_documents and, as SelectionFilters must equal them, in every choice by filter (read_documents, bulk, run_workflow).

Criteria proven: full hosted E2E 45661300031 on 21cdf55c, all ten jobs green (contract tests on Paperless 3.5.1 and E2E of parts b, c and d, which that head contains). Definition of Done: tests first is left open for single tests written with or after their code (guard test, mutant kills); just check was not run as a whole locally; merge open.
<!-- SECTION:NOTES:END -->
