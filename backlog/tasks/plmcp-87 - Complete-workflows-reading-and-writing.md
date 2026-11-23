---
id: PLMCP-87
title: Complete workflows, reading and writing
status: To Do
assignee: []
created_date: '2026-11-23T12:22:01Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 70100
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset workflows (off by default) already reads, creates, changes, switches on and off (enabled), reorders (order) and deletes workflows; the owner's agent did not see it. An update replaces the whole list of triggers or actions, and the single-object endpoints know no owning workflow, so a write there would bypass decision-21. Split from PLMCP-81. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Reading: single triggers and actions are readable, and the workflows that use an object (tag, correspondent, type, path, field, user, group, mail rule) can be found
- [ ] #2 Writing: one trigger or action can be added, changed or removed without resending the others; switching on and off and reordering keep working
- [ ] #3 Why a workflow did not run on a document is explained from its settings, document history and logs; the API keeps no run record beyond that, and the answer says so
- [ ] #4 Every path keeps decision-21 (confirm, hidden headers and passwords, unwritable types) or a superseding decision says otherwise
- [ ] #5 Every endpoint of workflows, workflow_triggers and workflow_actions in the 3.4 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-83 once it exists)
- [ ] #6 Contract tests on the fake and Paperless 3.4.2, tool tests and E2E cover each new read and write, the decision-21 refusals on the new paths and hidden values
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
