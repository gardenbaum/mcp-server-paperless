---
id: PLMCP-81.1
title: 'Mail a: reading processed mail, statuses, OAuth guard, exclusions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:53Z'
updated_date: '2026-11-25T07:31:55Z'
labels:
  - mail
dependencies: []
parent_task_id: PLMCP-81
priority: medium
type: feature
ordinal: 99000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-81: one processed mail and its full error cannot be read, the third status Paperless writes cannot be filtered for, update_mail_account can overwrite the access token of an OAuth account, and what a mail server answers to a login can hold the password. Design approved by the owner on 2026-11-24 (decisions 1 to 12 as recommended); file design-plmcp-81.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_processed_mail reads one processed mail with its full error (20,000 characters a call, continued) and says what would happen if its record were deleted
- [ ] #2 list_processed_mail filters the status PROCESSED_WO_CONSUMPTION, takes an order and shows the uid
- [ ] #3 The password of a token account is refused before anything is sent and the tool texts say that OAuth accounts are set up in the web UI and a saved account can be tested
- [ ] #4 What the mail server answered to a login is in no result, error or log line of this server
- [ ] #5 The owner-approved exclusions of PLMCP-81 are in docs/api-coverage.toml with quote and date
- [ ] #6 Contract tests on the fake and Paperless 3.5.1 and tool tests cover the new reads and refusals
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
1. domain/urls.py: the answer of a mail server to a login is not shown (inside text_without_credentials and lines_without_credentials, so errors, tasks, logs and raw answers are covered). 2. Port: MailReader.get_processed_mail, ordering of list_processed_mail, uid, owner, third status; MailWriter.update_mail_account refuses a password for a token account right before the PATCH. Adapter and fake; adapter and contract tests first. 3. services/processed_mail.py: list, one mail with a slice of its error, the refetch sentence of design 2.2 with siblings. 4. Tools: get_processed_mail, ordering, texts of T5, T6 and OAuth. 5. Decisions (token accounts; login answers), exclusions in the coverage table, docs, snapshots, guide limit. 6. Draft PR early, one full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24): designs 81, 87, 88, 89, 90, 83, 100 all as recommended; here decisions 1, 2a, 3a, 4a (exclusions on POST), 5a, 6a, 10, 12.
Login answers (decision-80) are hidden to the end of a text, and the undated head of a log in full, after an automated security review found that an answer can forge the end of its error and that a cut head hid nothing (main agent, 2026-11-24: fail closed, tests first). Order differs from the design: answers before URLs, because a URL before could swallow where the answer starts.

Owner's words (2026-11-24): 'Alle wie empfohlen' in the question 'Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?'. Coverage rows and decisions 79 and 80 quote exactly that now.

Review round 1 (independent), tests first: M1 an answer that starts with a carriage return was shown in get_log and raw logs (confirmed leak, fixed); M2 'Error while retrieving mailbox NAME:' hidden as well, with what useful text is lost, and the residuals, in decision-80; M3 tests for the surviving mutants; L1 is_token is bound in confirmations; L2 other records are always looked for, caveats named; L3 a failing list does not fail get_processed_mail; L4 (real order) with the mail server in 81.2.
<!-- SECTION:NOTES:END -->
