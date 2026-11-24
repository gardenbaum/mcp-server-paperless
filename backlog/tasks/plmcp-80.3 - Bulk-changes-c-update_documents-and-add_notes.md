---
id: PLMCP-80.3
title: 'Bulk changes c: update_documents and add_notes'
status: To Do
assignee: []
created_date: '2026-11-24T12:54:34Z'
labels:
  - bulk
dependencies:
  - PLMCP-80.1
parent_task_id: PLMCP-80
priority: medium
type: feature
ordinal: 94000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-80: different values need one call per document. Design approved by the owner on 2026-11-24 (decisions 1 to 20 as recommended); file design-plmcp-80.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 update_documents changes up to 100 documents with individual values in one call, every field update_document knows, with preview, confirmation and a result per document
- [ ] #2 Everything that can be known beforehand is checked before the first write; a failure names what was and was not changed
- [ ] #3 add_notes adds notes to many documents with a result per document and skips a note whose text is already there
- [ ] #4 Contract, tool and E2E tests cover limits, rate, partial failures and a restricted user
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
