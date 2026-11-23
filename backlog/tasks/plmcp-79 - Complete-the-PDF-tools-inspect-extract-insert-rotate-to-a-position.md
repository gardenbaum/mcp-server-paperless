---
id: PLMCP-79
title: 'Complete the PDF tools: inspect, extract, insert, rotate to a position'
status: To Do
assignee: []
created_date: '2026-11-23T12:14:50Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 67000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset pdf (off by default) already rotates, merges, splits, deletes pages, edits, removes passwords and reprocesses. Example need: one scan holds a contract and two payment slips. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Reading: page count, page sizes, rotation and a page overview (with the page images of their own task) help decide where to split
- [ ] #2 Writing: extract pages into a new document while keeping or removing them in the source, insert pages of one document into another, reorder, rotate single pages, with metadata and notes handled as stated
- [ ] #3 Every operation has a preview, names what is lost, needs confirmation where it is final, and can create a version instead of replacing
- [ ] #4 A split can set title, type, correspondent, date and tags per part
- [ ] #5 Every PDF operation of the 3.4 API is covered or its omission recorded with a reason
- [ ] #6 Live tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
