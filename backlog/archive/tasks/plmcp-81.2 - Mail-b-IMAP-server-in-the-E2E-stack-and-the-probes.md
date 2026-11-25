---
id: PLMCP-81.2
title: 'Mail b: IMAP server in the E2E stack and the probes'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:55Z'
updated_date: '2026-11-25T14:46:40Z'
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
- [x] #1 A job of its own runs the live mail tests against a real IMAP server, pinned by digest and followed by Renovate
- [x] #2 P1 to P4 of the design are answered by live tests, with the texts of Paperless; a probe that fails stops the part that needs it
- [x] #3 The redaction of login answers is proven against a real failed login
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
1. Overlay compose.imap.yaml (Dovecot by digest), just e2e-imap, job in e2e-full.yml, marker imap_e2e. 2. Probes P1 to P4 as live tests in tests/e2e/test_mail_import.py (with what Paperless said in the report). 3. A temporary workflow runs only them on the draft PR; it and -rP go before the PR is ready. 4. What differs from the table of the design stops PLMCP-81.3 and is reported.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Hosted evidence on Paperless 3.5.1 with Dovecot 2.4.5: temporary workflow (deleted again) runs 45507641847, 45508732726, 45509709915 (found: see below) and 45510669382 (18 of 18 green).
P1: every row of the table of the design (2.2) is proven, so is what get_processed_mail says in refetch: SUCCESS with mark_read, flag, tag and an Apple colour (neither mark nor record alone lets the mail in again), delete, move (new uid, fetched with its record kept), FAILED (T4: the good attachment is a document, the action did not run, the refetch takes it again and then acts, also delete), PROCESSED_WO_CONSUMPTION (also when all attachments are excluded), disabled rule, two records of eml_and_attachments (either blocks), T7.
P2: T1 as read; empty list and 1,001 unknown ids are 200; a foreign id is 403 for all and nothing is deleted; a user with delete_processedmail only deletes an unowned record and may not read records (403), so delete_processed_mail (81.3) needs view_processedmail as well.
P3: the tracebacks, mail.log and paperless.log are as the redaction of decision-80 expects; nothing of a real failed login (during a fetch and during the action) is shown by get_processed_mail, list_processed_mail, get_log or paperless_api_get. DIFFERENT from the design: result_data of a mail_fetch task is null in the API, so the texts of T5 ('Skipped', 'No new documents were added.', 'Added n document(s).') cannot be read; only paperless.log tells. This concerns the tasks part of explain_mail_import (81.4). A failed file gives an error that starts with the chain of a ChordError or of the parser.
P4: Dovecot signs in without setup and has UIDVALIDITY, keywords and MOVE; the whole job took 5 to 8 minutes (design: 1 to 2).

Owner, 2026-11-25: 'Überall wie von dir empfohlen bitte.': the job e2e-imap stays complete at 4 to 5 minutes.

Review round 1, tests first: servers that repeat the credentials (LOGIN, AUTHENTICATE PLAIN for a password that is not ASCII, XOAUTH2 for a token; NO, untagged BYE with a carriage return, NO to LOGOUT; also in the record of an action), a stub in the tests beside Dovecot; a live test of every order; the log tail test first proves that the answer is in the tail.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A job of its own (e2e-imap) runs the live mail tests against Dovecot 2.4.5, pinned by digest with a Renovate rule. P1 to P4 are answered by live tests (notes); P3 found that mail_fetch tasks have no text in the API, which 81.4 takes into account. Real failed logins prove the redaction of decision-80. Merged as 257fdb1 (#156). e2e-imap: 39 tests green in 45630847369 (main 1efb5cd), 60 in 45632136477. DoD 1 stays open: test-only part, the probes were not seen red. DoD 2 stays open: no local run of the whole just check is recorded. On GitHub the check jobs run its recipes one by one: 45583889907 (290e2e5).
<!-- SECTION:FINAL_SUMMARY:END -->
