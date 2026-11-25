---
id: PLMCP-119
title: Close the open test criteria of PLMCP-79 to 88
status: To Do
assignee: []
created_date: '2026-11-25T14:53:39Z'
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
- [ ] #1 PLMCP-79 AC 8, contract (fake and 3.5.1): preview, refusal and failed steps of insert_pages, extract_pages and split_document with metadata
- [ ] #2 PLMCP-79 AC 8, tool: tasks the user may not see for insert_pages, extract_pages and split_document; failed steps of extract_pages and split_document
- [ ] #3 PLMCP-79 AC 8, E2E: failed steps of insert_pages, extract_pages and split_document; tasks the user may not see for insert_pages and extract_pages
- [ ] #4 PLMCP-80 AC 6, contract (fake and 3.5.1): the rate (HTTP 429 ends a bulk call and sends nothing more) and a changed set between preview and confirm
- [ ] #5 PLMCP-80 AC 6, tool: the rate
- [ ] #6 PLMCP-80 AC 6, E2E: the rate
- [ ] #7 PLMCP-81.3 AC 3, contract (fake and 3.5.1): the refetch and unknown outcomes of deleting records
- [ ] #8 PLMCP-81.3 AC 3, tool: the refetch, partial and unknown outcomes and a restricted user of delete_processed_mail
- [ ] #9 PLMCP-81.3 AC 3, E2E with the IMAP server: partial and unknown outcomes of delete_processed_mail
- [ ] #10 PLMCP-81.4 AC 3, contract (fake and 3.5.1): explain_mail_import with hidden rules, for a user who is not staff, and that no credential appears
- [ ] #11 PLMCP-81.5 AC 3, tool: set_permissions on mail accounts and rules as owner who is no superuser and as non-owner with a change grant, and on mail rules as superuser
- [ ] #12 PLMCP-81.5 AC 3, E2E: set_permissions on mail accounts and rules as owner who is no superuser and as non-owner with a change grant
- [ ] #13 PLMCP-81 AC 7, contract, tool and E2E: what the criteria for 81.3, 81.4 and 81.5 above name
- [ ] #14 PLMCP-87.1 AC 6, contract (fake and 3.5.1): the search for used objects and the workflows that hold a single part
- [ ] #15 PLMCP-87.1 AC 6, tool: the search for a used correspondent, document type, storage path, custom field, user and group
- [ ] #16 PLMCP-87.1 AC 6, E2E: the search finding a used correspondent, document type, storage path, user, group and mail rule
- [ ] #17 PLMCP-87.2 AC 4, contract (fake and 3.5.1): removing one trigger, and the refusals of decision-21 and decision-66 for the part operations
- [ ] #18 PLMCP-87.2 AC 4, tool: adding, removing and moving a trigger and moving an action with update_workflow_part
- [ ] #19 PLMCP-87.2 AC 4, E2E: changing a trigger with update_workflow_part
- [ ] #20 PLMCP-87.4 AC 3, contract (fake and 3.5.1): explain_workflow for a matching workflow, one that does not match and a restricted user
- [ ] #21 PLMCP-87.4 AC 3, tool: explain_workflow for a matching workflow and for a restricted user who gets an answer with gaps
- [ ] #22 PLMCP-87 AC 6, contract, tool and E2E: what the criteria for 87.1, 87.2 and 87.4 above name
- [ ] #23 PLMCP-88.2 AC 4, contract (fake and 3.5.1): pinning and unpinning a view
- [ ] #24 PLMCP-88.2 AC 4, tool: unpinning a view
- [ ] #25 PLMCP-88.2 AC 4, E2E over stdio and over HTTP: missing permissions
- [ ] #26 PLMCP-88.3 AC 4, contract (fake and 3.5.1): owner, non-owner, superuser and unowned views with and without confirm
- [ ] #27 PLMCP-88.3 AC 4, tool: set_permissions on views as owner who is no superuser, as non-owner and on an unowned view, with and without confirm
- [ ] #28 PLMCP-88.3 AC 4, E2E: set_permissions on views as owner who is no superuser, as non-owner and on an unowned view, with and without confirm
- [ ] #29 PLMCP-88 AC 5, contract (fake and 3.5.1): sharing a view with and without confirm
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
