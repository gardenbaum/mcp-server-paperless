---
id: PLMCP-87.4
title: 'Workflows d: explain_workflow'
status: To Do
assignee: []
created_date: '2026-11-24T22:43:07Z'
labels:
  - workflows
dependencies:
  - PLMCP-87.1
parent_task_id: PLMCP-87
priority: medium
type: feature
ordinal: 102000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth part of PLMCP-87: why a workflow did not run on a document can only be guessed; Paperless keeps no readable record of runs. Design approved by the owner on 2026-11-24, all recommendations (file design-plmcp-87.md of the main agent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 explain_workflow compares settings, document, history, consume task and logs and says what is inferred and what cannot be known
- [ ] #2 Log lines show no text or names of other documents
- [ ] #3 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover matching, not matching and restricted users
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
