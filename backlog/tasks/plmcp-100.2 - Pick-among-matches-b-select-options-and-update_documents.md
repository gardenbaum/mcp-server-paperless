---
id: PLMCP-100.2
title: 'Pick among matches b: select options and update_documents'
status: To Do
assignee: []
created_date: '2026-11-25T17:37:40Z'
updated_date: '2026-11-25T17:38:05Z'
labels:
  - tools
dependencies:
  - PLMCP-100.1
parent_task_id: PLMCP-100
priority: low
type: enhancement
ordinal: 113000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-100 (design-plmcp-100.md, approved with all recommendations on 2026-11-24): two options with one label cannot be told apart today, and update_documents collects the errors of its items, so the question gets lost there.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 On a write an ambiguous label of a select option lets the user pick an option, for a document's value and for an option to rename or remove, also when labels are equal
- [ ] #2 update_documents, and upload_documents for the metadata of the whole batch, ask when every refused item is ambiguous, and keep the present collected error otherwise
- [ ] #3 Decline, cancel, malformed answers, reads and dry runs keep the present error; nothing is written twice
- [ ] #4 Tests at service, tool (both protocol eras, stdio and HTTP) and E2E level
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
