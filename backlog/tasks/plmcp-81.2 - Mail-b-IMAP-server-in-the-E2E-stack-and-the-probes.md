---
id: PLMCP-81.2
title: 'Mail b: IMAP server in the E2E stack and the probes'
status: To Do
assignee: []
created_date: '2026-11-24T22:44:55Z'
labels:
  - mail
dependencies:
  - PLMCP-81.1
parent_task_id: PLMCP-81
priority: medium
type: feature
ordinal: 100000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-81, test only: the E2E stack has no IMAP server, so a fetch, a refetch and the actions of rules were never seen. Before the deletion of records. Design approved by the owner on 2026-11-24 (decisions 1 to 12 as recommended); file design-plmcp-81.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A job of its own runs the live mail tests against a real IMAP server, pinned by digest and followed by Renovate
- [ ] #2 P1 to P4 of the design are answered by live tests, with the texts of Paperless; a probe that fails stops the part that needs it
- [ ] #3 The redaction of login answers is proven against a real failed login
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
