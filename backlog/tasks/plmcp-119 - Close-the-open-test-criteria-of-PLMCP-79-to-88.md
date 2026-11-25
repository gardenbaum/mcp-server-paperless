---
id: PLMCP-119
title: Close the open test criteria of PLMCP-79 to 88
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T14:53:39Z'
updated_date: '2026-11-25T17:35:44Z'
labels:
  - testing
dependencies: []
priority: medium
type: task
ordinal: 107000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The code of PLMCP-79, 80, 81, 87 and 88 is merged, but these tasks stay open because a criterion that names several test levels is read literally: every item has to be covered at every named level (main agent, 2026-11-25; a more lenient reading would be the owner's decision). Each criterion here names the task, its criterion and the level of missing tests; the task itself is ticked when its tests are on main. The criteria of the tasks are not changed. Not tests and therefore not here: PLMCP-81.1 AC 4 (the residual of decision-80: a forged log record), on which AC 7 of PLMCP-81 depends too, and PLMCP-79 AC 7 (from_webui, with PLMCP-83). A test that cannot be made at its level is put to the owner, never left out silently.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 PLMCP-79 AC 8, contract (fake and 3.5.1): preview, refusal and failed steps of insert_pages, extract_pages and split_document with metadata
- [x] #2 PLMCP-79 AC 8, tool: tasks the user may not see for insert_pages, extract_pages and split_document; failed steps of extract_pages and split_document
- [x] #3 PLMCP-79 AC 8, E2E: failed steps of insert_pages, extract_pages and split_document; tasks the user may not see for insert_pages and extract_pages
- [x] #4 PLMCP-80 AC 6, contract (fake and 3.5.1): the rate (HTTP 429 ends a bulk call and sends nothing more) and a changed set between preview and confirm
- [x] #5 PLMCP-80 AC 6, tool: the rate
- [ ] #6 PLMCP-80 AC 6, E2E: the rate
- [x] #7 PLMCP-81.3 AC 3, contract (fake and 3.5.1): the refetch and unknown outcomes of deleting records
- [x] #8 PLMCP-81.3 AC 3, tool: the refetch, partial and unknown outcomes and a restricted user of delete_processed_mail
- [x] #9 PLMCP-81.3 AC 3, E2E with the IMAP server: partial and unknown outcomes of delete_processed_mail
- [x] #10 PLMCP-81.4 AC 3, contract (fake and 3.5.1): explain_mail_import with hidden rules, for a user who is not staff, and that no credential appears
- [x] #11 PLMCP-81.5 AC 3, tool: set_permissions on mail accounts and rules as owner who is no superuser and as non-owner with a change grant, and on mail rules as superuser
- [x] #12 PLMCP-81.5 AC 3, E2E: set_permissions on mail accounts and rules as owner who is no superuser and as non-owner with a change grant
- [x] #13 PLMCP-81 AC 7, contract, tool and E2E: what the criteria for 81.3, 81.4 and 81.5 above name
- [x] #14 PLMCP-87.1 AC 6, contract (fake and 3.5.1): the search for used objects and the workflows that hold a single part
- [x] #15 PLMCP-87.1 AC 6, tool: the search for a used correspondent, document type, storage path, custom field, user and group
- [x] #16 PLMCP-87.1 AC 6, E2E: the search finding a used correspondent, document type, storage path, user, group and mail rule
- [x] #17 PLMCP-87.2 AC 4, contract (fake and 3.5.1): removing one trigger, and the refusals of decision-21 and decision-66 for the part operations
- [x] #18 PLMCP-87.2 AC 4, tool: adding, removing and moving a trigger and moving an action with update_workflow_part
- [x] #19 PLMCP-87.2 AC 4, E2E: changing a trigger with update_workflow_part
- [x] #20 PLMCP-87.4 AC 3, contract (fake and 3.5.1): explain_workflow for a matching workflow, one that does not match and a restricted user
- [x] #21 PLMCP-87.4 AC 3, tool: explain_workflow for a matching workflow and for a restricted user who gets an answer with gaps
- [x] #22 PLMCP-87 AC 6, contract, tool and E2E: what the criteria for 87.1, 87.2 and 87.4 above name
- [x] #23 PLMCP-88.2 AC 4, contract (fake and 3.5.1): pinning and unpinning a view
- [x] #24 PLMCP-88.2 AC 4, tool: unpinning a view
- [x] #25 PLMCP-88.2 AC 4, E2E over stdio and over HTTP: missing permissions
- [x] #26 PLMCP-88.3 AC 4, contract (fake and 3.5.1): owner, non-owner, superuser and unowned views with and without confirm
- [x] #27 PLMCP-88.3 AC 4, tool: set_permissions on views as owner who is no superuser, as non-owner and on an unowned view, with and without confirm
- [x] #28 PLMCP-88.3 AC 4, E2E: set_permissions on views as owner who is no superuser, as non-owner and on an unowned view, with and without confirm
- [x] #29 PLMCP-88 AC 5, contract (fake and 3.5.1): sharing a view with and without confirm
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
One PR per area, one at a time, each from main: 79 (AC 1-3), 80 (4-6), 81 (7-13), 87 (14-22), 88 (23-29). Failing steps and other people's changes are made by a port wrapper (constructor seam) that acts on the same Paperless just before a named call: the same assertions on the fake and 3.5.1. Every PR: hand mutants on an archive copy, full hosted E2E, ticks after the live run.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner, 2026-11-26, verbatim: "überall wie von dir empfohlen" (translation: "everywhere as you recommend"). Question (a): does "refetch" at contract and tool level count as proven by what the tools say is fetched again and by the record going, the real refetch being proven in E2E with the IMAP server, since neither the fake nor the contract stack has a mail server? Recommended and decided: yes, with this note.

Owner, 2026-11-26, verbatim: "überall wie von dir empfohlen" (translation: "everywhere as you recommend"). Question (b): does a 429 from a transport in front of the real Paperless count as "against 3.5.1", since 3.5.1 throttles only its sign-in? Recommended and decided: yes, with this note.

The owner's answer above was given to and relayed by the main agent; the quote is as relayed.

Owner, 2026-11-26, verbatim: "Bitte alles wie von dir empfohlen." (translation: "Please everything as you recommend."). Question (AC 18 of PLMCP-119): triggers have no order, so is the tool test of moving a trigger, which proves the refusal, what the criterion asks? Recommended and decided: yes. The answer was given to and relayed by the main agent; the quote is as relayed.

Owner, 2026-11-26, verbatim: "Bitte alles wie von dir empfohlen." (translation: "Please everything as you recommend."). Question (AC 9 of PLMCP-119): may "still there" be produced in E2E by re-creating the record via the ORM of Paperless just before the read-back? Recommended and decided: yes. The answer was given to and relayed by the main agent; the quote is as relayed.

Ticked after full hosted E2E 45656414763 on 7d92f28b (top of the stack, 9c06ef4 of main merged): contract modules pdf_steps 4, bulk_limits 12, mail_steps 10, workflow_steps 20, view_steps 10 live tests green, E2E pdf_steps 3, mail 14, workflows 13, view_pins 11 green, mail_import with IMAP green but test_a_server_that_forges_the_start_of_a_log_record[plain-*], which is red on main too (45654754686). AC 6 stays open: test_a_bulk_edit_whose_task_cannot_be_followed... did not allow for the retries of the client (decision-71); fixed, not rerun. After that run only weights and that assertion changed. AC 7, 8: refetch as the owner decided, real refetch in E2E with IMAP. AC 4 to 6: 429 from a transport in front of the real Paperless, as the owner decided.
<!-- SECTION:NOTES:END -->
