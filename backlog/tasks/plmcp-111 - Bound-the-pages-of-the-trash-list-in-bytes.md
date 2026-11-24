---
id: PLMCP-111
title: Bound the pages of the trash list in bytes
status: To Do
assignee: []
created_date: '2026-11-24T09:06:02Z'
labels:
  - safety
dependencies: []
priority: low
type: bug
ordinal: 90000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
list_trash pages (100 rows with full texts) have no byte limit and accept compressed answers; restore, empty by ids, get_trashed_file and the ids filter fall back to the same request. A plain bound would stop restore by ids behind a compressing proxy. Found by PLMCP-78.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every read of whole trash rows is bounded, also when the answer is compressed, and restore by ids still works behind a compressing proxy
- [ ] #2 Adapter, service and contract tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
