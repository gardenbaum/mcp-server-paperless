---
id: PLMCP-69
title: Show a short demo recording at the top of the README
status: To Do
assignee: []
created_date: '2026-11-23T11:37:58Z'
labels: []
dependencies: []
priority: medium
type: enhancement
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Visitors decide in seconds. A short recording at the top of the README shows what the server does. Owner approved a GIF with invented documents on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The README shows a GIF of at most 30 seconds near the top, with alt text, that also renders on PyPI
- [ ] #2 It shows answers of the real released tools against a paperless-ngx instance with invented documents only, and no personal data
- [ ] #3 The recording is reproducible from a script or a documented recipe in the repository, and the file is small enough to load quickly (at most 3 MB)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
