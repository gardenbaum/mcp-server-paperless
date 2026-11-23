---
id: PLMCP-80
title: 'Complete bulk changes: individual values and selection by search'
status: To Do
assignee: []
created_date: '2026-11-23T12:14:50Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 68000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset bulk (off by default) sets the same values on many documents. Different values per document need one call each. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One call changes many documents with individual values each, every field update_document knows, with a dry run, confirmation and a result per document; a failure names what was and was not changed
- [ ] #2 Documents can be chosen by ids, by the search filters or by a saved view, with the matched set shown first and protected against changes in between
- [ ] #3 Reading many documents in one call is possible (fields selectable) within a size limit
- [ ] #4 Every bulk method of the 3.4 API is covered or its omission recorded with a reason; notes can be added in bulk
- [ ] #5 Limits, rate and partial failures are tested, also live
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
