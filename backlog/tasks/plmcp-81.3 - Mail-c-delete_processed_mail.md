---
id: PLMCP-81.3
title: 'Mail c: delete_processed_mail'
status: To Do
assignee: []
created_date: '2026-11-24T22:44:56Z'
labels:
  - mail
dependencies:
  - PLMCP-81.1
  - PLMCP-81.2
parent_task_id: PLMCP-81
priority: medium
type: feature
ordinal: 101000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-81: records of processed mail cannot be deleted, so a failed mail is never fetched again. Design approved by the owner on 2026-11-24 (decisions 1 to 12 as recommended); file design-plmcp-81.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 delete_processed_mail deletes 1 to 100 records after a preview and a confirmation that names each mail, what is fetched again and when, and what the rule then does
- [ ] #2 Only a second read says a record is deleted; a missing id refuses the call
- [ ] #3 Contract, tool and E2E tests with the IMAP server cover the refetch, partial and unknown outcomes and a restricted user
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
