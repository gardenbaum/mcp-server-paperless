---
id: PLMCP-80.1
title: 'Bulk changes a: choose documents by ids, filters or view, read_documents'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:32Z'
updated_date: '2026-11-24T13:43:01Z'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Domain: SelectionFilters (the 21 filters of search_documents), Selection, projected row and report. 2. Port select_documents and read_rows: adapter, fake, adapter and contract tests (the probes P1, P6 reading part, P9 reading part, P10 as live contract tests). 3. services/selection.py (resolve, refuse, compare with given ids) and services/read_documents.py. 4. Tool read_documents in bulk, registry, texts, docs, E2E. Red first at every level.
<!-- SECTION:PLAN:END -->
