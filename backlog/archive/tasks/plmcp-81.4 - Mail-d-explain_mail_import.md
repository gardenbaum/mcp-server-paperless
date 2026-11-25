---
id: PLMCP-81.4
title: 'Mail d: explain_mail_import'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:57Z'
updated_date: '2026-11-25T14:46:40Z'
labels:
  - mail
dependencies:
  - PLMCP-81.1
parent_task_id: PLMCP-81
priority: medium
type: feature
ordinal: 102000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth part of PLMCP-81: why a mail was not imported must be gathered by hand from settings, processed mail, tasks and logs. Design approved by the owner on 2026-11-24 (decisions 1 to 12 as recommended); file design-plmcp-81.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 explain_mail_import explains from settings, processed mail, tasks and logs without a connection of this server, says it is not a dry run and names the closest way
- [x] #2 Each check says ok, no or unknown; bounds and what could not be read are named
- [x] #3 Contract, tool and E2E tests cover each check, hidden rules, a user who is not staff, and that no credential appears
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
1. services/log_records.py of PLMCP-87.4, verbatim. 2. domain (MailExplanation), services/mail_explanation.py: settings against what the caller knows, processed mail, tasks, logs (staff: mail log for the rule's lines, paperless log for skipped fetches, else not known), bounds. 3. Tool explain_mail_import, read-only. 4. Service, property, tool tests first; E2E with the mail server. 5. decision, docs, guide 2988 to 3010.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner, 2026-11-25: 'Überall wie von dir empfohlen bitte.': mail_fetch tasks say nothing in the API (result_data is null, PLMCP-81.2), so a skipped fetch is taken from paperless.log only, staff only, else 'not known'.

Implementation notes: the service was written before its tests, against the rule; afterwards 39 hand mutants of services/mail_explanation.py were run: 8 survived, were killed by new tests and re-run; services/mail_explanation.py and log_records.py 100 % with branches in the first run before the last tests. services/log_records.py is PLMCP-87.4's (local 7fcc37c) with a shorter docstring. Owner, 2026-11-25: skipped fetches from paperless.log, staff only, else not known. Read as: the log mail (design 7a) is used for the lines of the account and its rules as well. decision-90 (the CLI gave 89, removed again). No new Paperless call: accounts, rules, processed mail, tasks and logs are read with existing ports.

Review of #163 fixed, tests first: forged log dates, log forms anchored with their loggers (services/mail_log_lines.py), one start time per log (logs_from is a list), gaps for failed tasks beyond the bound, bounded gaps, check record for records that can block the mail, texts of 2000 characters for sender, recipient and subject, extreme dates, two days of doubt, colours, folder to move to, exact folder for queued mail. 84 hand mutants killed. Limits: hidden records and tasks, the uid of a mail, names with a full stop, new log wordings, same subject in another account.

Second review, tests first: another mail's line with the same file name is left out, wording of skipped fetches and of records of others, contract test of the log lines of a fetch, boundaries of 5 files and 18 gaps, decision-90 names forged records and lines that may be about another mail.

Codex P2 on #163: fetch tasks beyond the five that are read stand in not_checked and make complete false, test first.

Owner, 2026-11-25: "Alles wie empfohlen" (everything as recommended): fetches before the newest five are named in not_checked and leave complete as it is; test first. This replaces the note before. Full E2E 45615787192 on 4254397 is green in all ten jobs.

Codex P2 on #163, test first: the rules are read page by page (MailReader.list_mail_rule_page, new), so no page is asked for 40 seconds after the call began; unread rules stand in not_checked, make complete false, and are not said to be missing.

AC 3: every check of a rule (enabled, folder, age, sender, recipient, subject, body, attachments, mark, target, record, earlier rules) in tool and contract tests (tests/explained.py) and in E2E with the mail server, where Paperless then does what was said; hidden rules, no staff and credentials too. Full E2E 45632136477 on 27cf967 green in all ten jobs (60 mail tests in 415 s, before 39 in 328 s). Notes about the account are no check of a rule: service and E2E.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
explain_mail_import (read-only) explains from settings, processed mail, tasks and logs why a mail was not imported, without a connection of this server; each check says ok, no or unknown, bounds and what could not be read are named (decision-90). Merged as 094970f (#163); every check in E2E, tool and contract tests as 760d3e9 (#171). Full E2E 45632136477 on 27cf967 green in all ten jobs. DoD 1 stays open: the service was written before its tests. DoD 2 stays open: no local run of the whole just check is recorded. On GitHub the check jobs run its recipes one by one: 45632143936 (27cf967, #171) and 45630847344 (main 1efb5cd, with #163) are green; the check jobs of the last heads of #163 and #171 were cancelled by their merges.
<!-- SECTION:FINAL_SUMMARY:END -->
