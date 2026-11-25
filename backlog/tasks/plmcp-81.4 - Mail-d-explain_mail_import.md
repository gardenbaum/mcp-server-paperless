---
id: PLMCP-81.4
title: 'Mail d: explain_mail_import'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:57Z'
updated_date: '2026-11-25T09:37:08Z'
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
- [ ] #1 explain_mail_import explains from settings, processed mail, tasks and logs without a connection of this server, says it is not a dry run and names the closest way
- [ ] #2 Each check says ok, no or unknown; bounds and what could not be read are named
- [ ] #3 Contract, tool and E2E tests cover each check, hidden rules, a user who is not staff, and that no credential appears
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
1. services/log_records.py of PLMCP-87.4, verbatim. 2. domain (MailExplanation), services/mail_explanation.py: settings against what the caller knows, processed mail, tasks, logs (staff: mail log for the rule's lines, paperless log for skipped fetches, else not known), bounds. 3. Tool explain_mail_import, read-only. 4. Service, property, tool tests first; E2E with the mail server. 5. decision, docs, guide 2988 to 3010.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner, 2026-11-25: 'Überall wie von dir empfohlen bitte.': mail_fetch tasks say nothing in the API (result_data is null, PLMCP-81.2), so a skipped fetch is taken from paperless.log only, staff only, else 'not known'.
<!-- SECTION:NOTES:END -->
