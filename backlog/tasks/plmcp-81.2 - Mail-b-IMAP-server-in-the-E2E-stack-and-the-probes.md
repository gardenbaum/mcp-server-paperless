---
id: PLMCP-81.2
title: 'Mail b: IMAP server in the E2E stack and the probes'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:55Z'
updated_date: '2026-11-24T23:39:40Z'
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
<!-- SECTION:NOTES:END -->
