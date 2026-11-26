---
id: PLMCP-122
title: Start on Windows
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T18:53:55Z'
updated_date: '2026-11-25T18:54:23Z'
labels:
  - tools
dependencies: []
priority: medium
type: bug
ordinal: 112000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The server does not start on Windows: services/upload_listing.py reads os.O_CLOEXEC when it is imported, which Windows does not have (found by the hosted Windows job on PLMCP-118). What protects uploads by path and saving on POSIX (dir_fd, O_NOFOLLOW) has no equivalent there: these are refused with a reason (decision-45), and must stay so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 python -m mcp_server_paperless --help and a stdio start with a tools/list work on Windows, proven by a hosted Windows job
- [ ] #2 Every other Windows-only obstacle at import or start is found and fixed or listed
- [ ] #3 Uploading by path and saving are no less safe than before: what has no equivalent on Windows is refused there with a reason
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
1. Red: tests/test_start.py in the hosted Windows job: every module imports, --help, a stdio start with tools/list, paths refused where they cannot be opened safely; tests/test_cli.py there too (HTTP start).
2. Green: what Windows lacks is asked for with getattr, as next to it; nothing of it is used where paths are refused. --help, which no platform had.
3. List what Windows lacks.
<!-- SECTION:PLAN:END -->
