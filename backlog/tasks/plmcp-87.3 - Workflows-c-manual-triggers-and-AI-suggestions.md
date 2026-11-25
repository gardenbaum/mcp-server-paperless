---
id: PLMCP-87.3
title: 'Workflows c: manual triggers and AI suggestions'
status: To Do
assignee: []
created_date: '2026-11-24T22:43:06Z'
labels:
  - workflows
dependencies:
  - PLMCP-87.2
parent_task_id: PLMCP-87
priority: medium
type: feature
ordinal: 101000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-87: run_workflow runs manual triggers, but only the web UI can make them; applying AI suggestions is only made there too. Design approved by the owner on 2026-11-24, all recommendations (file design-plmcp-87.md of the main agent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Manual triggers and the action applying AI suggestions can be created and changed, the latter confirmed while enabled
- [ ] #2 Password removal stays with the web UI and is recorded as an owner-approved exclusion
- [ ] #3 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover both types
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
