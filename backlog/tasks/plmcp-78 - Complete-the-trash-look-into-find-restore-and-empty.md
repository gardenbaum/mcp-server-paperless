---
id: PLMCP-78
title: 'Complete the trash: look into, find, restore and empty'
status: To Do
assignee: []
created_date: '2026-11-23T12:14:49Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 66000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset trash (off by default) already lists, restores and empties. The owner's agent did not see it. Missing is what the API allows beyond it. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every field the API gives for a trashed document is readable, and the list can be narrowed by title, type, date deleted and id across all pages
- [ ] #2 Where Paperless serves them, text, thumbnail and file of a trashed document are readable; where not, the answer says so
- [ ] #3 Several entries can be restored or deleted for good in one call by id or by a narrowed list, with a preview, confirmation and a result per entry
- [ ] #4 Deleting a document tells how to restore it, and which toolset that needs
- [ ] #5 Whether reading and restoring belong in the default profile is decided against the tool budget
- [ ] #6 Live tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
