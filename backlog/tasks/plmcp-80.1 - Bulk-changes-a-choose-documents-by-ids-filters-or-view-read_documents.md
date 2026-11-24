---
id: PLMCP-80.1
title: 'Bulk changes a: choose documents by ids, filters or view, read_documents'
status: To Do
assignee: []
created_date: '2026-11-24T12:54:32Z'
labels:
  - bulk
dependencies:
  - PLMCP-79.3
parent_task_id: PLMCP-80
priority: medium
type: feature
ordinal: 92000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-80: nobody can read many documents in one call, and a set chosen by search cannot be shown or protected against changes. Design approved by the owner on 2026-11-24 (decisions 1 to 20 as recommended); file design-plmcp-80.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 read_documents reads up to 100 documents per call by ids, search filters or saved view, with selectable fields, content only on request and at most 20000 characters of text and 100000 of answer per call
- [ ] #2 At most 1000 documents may match; every match is named and a changed set between preview and confirm is refused
- [ ] #3 Contract, tool and E2E tests cover limits and a restricted user
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
