---
id: PLMCP-80.2
title: 'Bulk changes b: bulk_edit_documents by filter and view, full scope and texts'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:33Z'
updated_date: '2026-11-24T15:17:42Z'
labels:
  - bulk
dependencies:
  - PLMCP-80.1
parent_task_id: PLMCP-80
priority: medium
type: feature
ordinal: 93000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-80: bulk_edit_documents takes ids only and its confirmation names three documents and a count. Design approved by the owner on 2026-11-24 (decisions 1 to 20 as recommended); file design-plmcp-80.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 bulk_edit_documents takes ids, search filters or a saved view and changes only documents that were shown
- [ ] #2 Confirmation texts name all documents, up to 100, and bind the current values
- [ ] #3 The coverage table quotes the owner-approved exclusions with their date
- [ ] #4 Contract, tool and E2E tests cover partial failures and a changed set
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
1. Probes as contract/E2E tests: P2 (Paperless's own filters, read-only through selection_data), P5 (delete and reprocess endpoints). 2. Domain: report fields, TaskFailed. 3. Adapter: documents/delete/ and documents/reprocess/. 4. Services: operation builders move to bulk_operations.py; BulkDocumentEditor takes a Selection, names every document, binds the current values, never acts by a filter alone, reads again after a failed, lost or unconfirmed change. 5. Tool arguments, texts, snapshots, docs. 6. Decisions, coverage exclusions, E2E. Red first at every level.
<!-- SECTION:PLAN:END -->
