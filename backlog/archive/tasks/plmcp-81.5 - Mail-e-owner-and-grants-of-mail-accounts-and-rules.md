---
id: PLMCP-81.5
title: 'Mail e: owner and grants of mail accounts and rules'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:58Z'
updated_date: '2026-11-25T14:46:40Z'
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
- [x] #1 set_permissions takes mail accounts and mail rules by id with the confirmation of decision-19 and says what a grant on an account gives
- [x] #2 The remaining rows of mail_accounts and mail_rules in docs/api-coverage.toml are covered
- [x] #3 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover owner, non-owner with a change grant and superuser
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. PermissionKind gains mail_account and mail_rule. Adapter: read one by one with full_perms (the mail views have no id__in filter), PATCH as for the others; schema guard entries with the reason. 2. Fake: grants of accounts and rules, visibility and change by them. 3. Service: ids only, no taxonomy cache, the note of what a change grant on an account gives. 4. Contract on fake and live (P5), tool, E2E. 5. decision-84, coverage rows, docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Review round 1, tests first: the note for accounts (POST mail_accounts/test/ sends the saved password anywhere at once) and one for rules; after a 400 to owner and grants together the object is read again, for every kind; 40 seconds for mail objects; PermissionKind is one flat Literal; contract tests for an owner who is no superuser, an unowned object, and what a refused owner leaves of the grants (a rule: set; a tag: not). decision-87 instead of 84 (the CLI gave it).

Review round 2, tests first: a refused named owner sets no grants (DRF's unique-together check runs first), only removing the owner beside an unowned twin sets them and then answers 400 (contract tests both ways, fake the same); the owner is compared after a 400; survivors of the read-back killed; the rule note names whole mails and attachments apart; a change grant on a rule is enough to make it delete mail (contract); the 40 seconds start with the call. Suppressions added in this task: PLR0913 on PermissionService.__init__ (the ports it uses) and on _halfway (what a failure leaves behind).

The rule note and the partial stop after 40 seconds are approved by the owner (2026-11-25, question dialog: "Ja, alles", yes, all of it).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
set_permissions takes mail accounts and rules by id with the confirmation of decision-19 and says what a change grant gives (decision-87). No row of mail_accounts or mail_rules is open in docs/api-coverage.toml. Owner, non-owner with a change grant, superuser and unowned objects: contract tests on the fake and 3.5.1 (tests/contract/test_permissions.py); the confirmation, sharing and handing over: tool tests and tests/e2e/test_mail.py (as superuser); what a grantee can do: tests/e2e/test_mail_import.py. Merged as f1c2cf2 (#162). CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. DoD 1 stays open: the PR records red runs for adapter to tool level, not for the live contract and E2E tests. DoD 2 stays open: no local run of the whole just check is recorded. On GitHub the check jobs run its recipes one by one: 45609771854 (80c28a1).
<!-- SECTION:FINAL_SUMMARY:END -->
