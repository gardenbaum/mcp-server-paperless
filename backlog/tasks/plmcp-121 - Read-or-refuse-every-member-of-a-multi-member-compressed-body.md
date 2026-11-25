---
id: PLMCP-121
title: Read or refuse every member of a multi-member compressed body
status: To Do
assignee: []
created_date: '2026-11-25T18:34:12Z'
labels:
  - safety
dependencies: []
priority: low
type: bug
ordinal: 112000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A gzip body with several members is legal, but a streamed download reads the first member only, silently (httpx2 did so before PLMCP-117, too; a page of the trash list refuses it). Reading all costs: about 15 lines in paperless/unpacking.py (a new decompressor when the data after the end starts like a member; zero padding and junk have to be told apart), a new zlib state per member (CPU for a body of many tiny members; memory stays bounded), and one answer that changes: a file that was short is then whole. Refusing is smaller but breaks a body that was read so far. Found by the review of PLMCP-117.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 read or refuse every member of a multi-member compressed body
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
