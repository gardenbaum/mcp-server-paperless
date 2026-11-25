---
id: PLMCP-81
title: Complete mail, reading and writing
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:51Z'
updated_date: '2026-11-25T14:46:41Z'
labels: []
dependencies:
  - PLMCP-91
priority: medium
type: feature
ordinal: 70000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset mail (off by default) already reads and writes accounts and rules, lists processed mail, tests a saved account and fetches mail; the owner's agent did not see it. Missing against the API: one processed mail, deleting processed-mail records, testing an unsaved account. The API has no rule dry run (one by this server would need its own IMAP connection, which decision-7 forbids) and no endpoint to create OAuth accounts (only the callback). Workflows, views, and tasks with history are tasks of their own. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Reading: one processed mail is readable with its full error
- [x] #2 Writing: processed-mail records can be deleted, singly and in bulk, with a preview and confirm; a live test states whether and when the mail is fetched again and what its rule then does to it
- [x] #3 Why a mail was not imported is explained from rule settings, processed mail and logs, without a connection from this server; the tool says it is not a dry run and names the closest way
- [x] #4 Testing an unsaved account (Paperless would connect to any host the model names) and creating an OAuth account or writing its token happen only after a decision that supplements or supersedes decision-18; until then the tools say that OAuth accounts are set up in the web UI and that a saved account can be tested
- [x] #5 OAuth accounts are read (type, token expiry), and their settings other than tokens are changed as far as /api/mail_accounts/ allows, without showing a token
- [x] #6 Every endpoint of mail_accounts, mail_rules, processed_mail and oauth in the vendored 3.5.1 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-91)
- [ ] #7 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each new read and write, the refusals, that no credential appears in a result, error or log, and the refetch
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24, question dialog, recommended options chosen): designs 81, 87, 88, 89, 90, 83, 100 all as recommended, with the guide limit in steps (2943 to 3010 for 81). Here design-plmcp-81.md, decisions 1 to 12: 1 leave out PUT and ignored bodies, 2a, 3a, 4a, 5a, 6a, 7a, 8a, 9a, 10 raise to at most 3010, 11a, 12 five parts (PLMCP-81.1 to 81.5).

Cleanup 2026-11-25: all five parts are merged (#155, #156, #158, #162, #163, #171); 81.2, 81.4 and 81.5 are Done. Open: 81.1 (AC 4) and 81.3 (AC 3), and for the same reasons AC 7: that no credential appears (the residual of decision-80: a forged log record) and the missing tests named in 81.3. DoD 1 and 2 are open in the parts; DoD 4: merged through the parts' pull requests, no own one. CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green.
<!-- SECTION:NOTES:END -->
