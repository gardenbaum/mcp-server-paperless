---
id: PLMCP-81.1
title: 'Mail a: reading processed mail, statuses, OAuth guard, exclusions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:53Z'
updated_date: '2026-11-25T17:39:28Z'
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
- [x] #1 get_processed_mail reads one processed mail with its full error (20,000 characters a call, continued) and says what would happen if its record were deleted
- [x] #2 list_processed_mail filters the status PROCESSED_WO_CONSUMPTION, takes an order and shows the uid
- [x] #3 The password of a token account is refused before anything is sent and the tool texts say that OAuth accounts are set up in the web UI and a saved account can be tested
- [ ] #4 the answer to a failed login is in no result, error or log line, except where a hostile server forges the start of a log record or repeats it outside the login
- [x] #5 The owner-approved exclusions of PLMCP-81 are in docs/api-coverage.toml with quote and date
- [x] #6 Contract tests on the fake and Paperless 3.5.1 and tool tests cover the new reads and refusals
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
1. domain/urls.py: the answer of a mail server to a login is not shown (inside text_without_credentials and lines_without_credentials, so errors, tasks, logs and raw answers are covered). 2. Port: MailReader.get_processed_mail, ordering of list_processed_mail, uid, owner, third status; MailWriter.update_mail_account refuses a password for a token account right before the PATCH. Adapter and fake; adapter and contract tests first. 3. services/processed_mail.py: list, one mail with a slice of its error, the refetch sentence of design 2.2 with siblings. 4. Tools: get_processed_mail, ordering, texts of T5, T6 and OAuth. 5. Decisions (token accounts; login answers), exclusions in the coverage table, docs, snapshots, guide limit. 6. Draft PR early, one full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24): designs 81, 87, 88, 89, 90, 83, 100 all as recommended; here decisions 1, 2a, 3a, 4a (exclusions on POST), 5a, 6a, 10, 12.
Login answers (decision-80) are hidden to the end of a text, and the undated head of a log in full, after an automated security review found that an answer can forge the end of its error and that a cut head hid nothing (main agent, 2026-11-24: fail closed, tests first). Order differs from the design: answers before URLs, because a URL before could swallow where the answer starts.

Owner's words (2026-11-24): 'Alle wie empfohlen' in the question 'Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?'. Coverage rows and decisions 79 and 80 quote exactly that now.

Review round 1 (independent), tests first: M1 an answer that starts with a carriage return was shown in get_log and raw logs (confirmed leak, fixed); M2 'Error while retrieving mailbox NAME:' hidden as well, with what useful text is lost, and the residuals, in decision-80; M3 tests for the surviving mutants; L1 is_token is bound in confirmations; L2 other records are always looked for, caveats named; L3 a failing list does not fail get_processed_mail; L4 (real order) with the mail server in 81.2.

Codex round: a forged marker on the line of the answer took the place of the real one and left the password before it visible (P1, fixed: the first marker of a line counts; property test with forged markers of every kind). Other records are looked for whatever the scope of the rule (in f103ada).

Owner, 2026-11-25: 'Überall wie von dir empfohlen bitte.' (everywhere as you recommend, please): decision-80 may hide more than 5a; accepted, residuals are in its consequences.

Cleanup 2026-11-25, merged as 99e01fa (#155). AC 4 stays open: decision-80 (consequences) says that a log line a server forges to look like the start of a record, in its answer to a login (carriage return, untagged BYE), is not told from a real one, so such an answer can still be shown. Missing: the owner's explicit word that this residual meets 'in no result, error or log line' (the recorded approval of 2026-11-25 is about hiding more), or code and a test that hide it. The other criteria: tests/services/test_processed_mail.py, tests/tools/test_mail.py, tests/contract/test_mail.py (fake and 3.5.1), docs/api-coverage.toml. CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. DoD 1 stays open: the PR records red runs for the domain to tool levels, not for the live contract and E2E tests. DoD 2 stays open: no local run of the whole just check is recorded. On GitHub the check jobs run its recipes one by one: 45575809989 (007bdcb).

AC 4 reworded by the owner, 2026-11-26: "Alles wie von dir empfohlen." (everything as you recommend), to the question whether the start of a log record is tightened and AC 4 says the answer to a failed login is in no result, error or log line, except where a hostile server forges the start of a log record or repeats it outside the login. decision-106 supplements decision-80 and names the two holes. The order of the criteria is kept.

Full E2E 45654754686 was red for AUTHENTICATE PLAIN: the echo server answered it with a BYE, whose text imaplib loses (socket error: EOF). The server now answers AUTHENTICATE with a tagged NO, or a BYE and a tagged answer; the cases that passed on the text of others now need their own. decision-108 supersedes that wording of decisions 106 and 80. No product change.
<!-- SECTION:NOTES:END -->
