---
id: PLMCP-81.5
title: 'Mail e: owner and grants of mail accounts and rules'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:58Z'
updated_date: '2026-11-25T09:48:54Z'
labels:
  - mail
dependencies: []
parent_task_id: PLMCP-81
priority: medium
type: feature
ordinal: 103000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fifth part of PLMCP-81: owner and grants of mail accounts and rules cannot be read or set. Before or with PLMCP-88. Design approved by the owner on 2026-11-24 (decisions 1 to 12 as recommended); file design-plmcp-81.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 set_permissions takes mail accounts and mail rules by id with the confirmation of decision-19 and says what a grant on an account gives
- [ ] #2 The remaining rows of mail_accounts and mail_rules in docs/api-coverage.toml are covered
- [ ] #3 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover owner, non-owner with a change grant and superuser
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
1. PermissionKind gains mail_account and mail_rule. Adapter: read one by one with full_perms (the mail views have no id__in filter), PATCH as for the others; schema guard entries with the reason. 2. Fake: grants of accounts and rules, visibility and change by them. 3. Service: ids only, no taxonomy cache, the note of what a change grant on an account gives. 4. Contract on fake and live (P5), tool, E2E. 5. decision-84, coverage rows, docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Review round 1, tests first: the note for accounts (POST mail_accounts/test/ sends the saved password anywhere at once) and one for rules; after a 400 to owner and grants together the object is read again, for every kind; 40 seconds for mail objects; PermissionKind is one flat Literal; contract tests for an owner who is no superuser, an unowned object, and what a refused owner leaves of the grants (a rule: set; a tag: not). decision-87 instead of 84 (the CLI gave it).
<!-- SECTION:NOTES:END -->
