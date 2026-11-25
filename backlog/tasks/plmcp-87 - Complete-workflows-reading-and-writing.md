---
id: PLMCP-87
title: Complete workflows, reading and writing
status: In Progress
assignee: []
created_date: '2026-11-23T12:22:01Z'
updated_date: '2026-11-25T14:53:15Z'
labels: []
dependencies:
  - PLMCP-91
priority: medium
type: feature
ordinal: 70100
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset workflows (off by default) already reads, creates, changes, switches on and off (enabled), reorders (order) and deletes workflows; the owner's agent did not see it. An update replaces the whole list of triggers or actions, and the single-object endpoints know no owning workflow, so a write there would bypass decision-21. Split from PLMCP-81. Requested by the owner on 2026-11-23. Paperless 3.5.1 (PLMCP-97) added the trigger type manual (5): the workflows tools read it and refuse to write it, and an update that replaces triggers refuses to delete one this server cannot write. Whether manual triggers can be written (a workflow with one is run by hand from the web UI), and running a workflow by hand (the bulk_edit method run_workflow, PLMCP-80), is decided here. GET /api/workflows/ also filters by enabled, id, id__in and trigger_type.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Reading: single triggers and actions are readable, and the workflows that use an object (tag, correspondent, type, path, field, user, group, mail rule) can be found
- [x] #2 Writing: one trigger or action can be added, changed or removed without resending the others; switching on and off and reordering keep working
- [x] #3 Why a workflow did not run on a document is explained from its settings, document history and logs; the API keeps no run record beyond that, and the answer says so
- [x] #4 Every path keeps decision-21 (confirm, hidden headers and passwords, unwritable types) or a superseding decision says otherwise
- [x] #5 Every endpoint of workflows, workflow_triggers and workflow_actions in the vendored 3.5.1 schema is covered; a proposed omission is put to the owner, and only an owner-approved exclusion, quoted with its date, is recorded (in the coverage table of PLMCP-91)
- [ ] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each new read and write, the decision-21 refusals on the new paths and hidden values
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
Reopened 2026-11-25: a criterion that names several test levels is read literally, every item at every named level. All parts merged (#154, #159, #160, #161), 87.3 archived. AC 1 to 5 are proven. AC 6 stays open for what 87.1 (AC 6), 87.2 (AC 4) and 87.4 (AC 3) miss. CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. DoD 1 is open in 87.4; DoD 2: see the parts; DoD 4: merged through the parts' pull requests, no own one.
<!-- SECTION:NOTES:END -->
