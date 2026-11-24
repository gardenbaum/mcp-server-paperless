---
id: PLMCP-110
title: Keep the copy whose file exists when resolving duplicates
status: To Do
assignee: []
created_date: '2026-11-24T08:50:41Z'
labels:
  - safety
dependencies: []
priority: medium
type: bug
ordinal: 85000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
resolve_duplicates with trash can keep the document whose file Paperless cannot find and trash the copy that still has it, because checksums come from the database. Restorable until the trash is emptied, but neither the preview nor the confirmation says so. Found by the review of PLMCP-105.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A kept document whose file Paperless cannot find is refused or named in preview, confirmation and result; the same for merging as versions
- [ ] #2 Contract tests on the fake and Paperless 3.5.1, service, tool and E2E tests cover it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
