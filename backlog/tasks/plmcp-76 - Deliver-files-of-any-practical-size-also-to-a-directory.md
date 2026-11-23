---
id: PLMCP-76
title: Deliver files of any practical size, also to a directory
status: To Do
assignee: []
created_date: '2026-11-23T12:14:47Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 64000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
download_document attaches files up to 512 KiB by default; larger ones are only linked. Uploads by path exist for stdio, downloads by path do not. bulk_download has the same limit question. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Over stdio, original, archive, versions and bulk ZIPs can be saved into explicitly allowed directories (off by default), with the path rules of PAPERLESS_MCP_UPLOAD_DIRS, without overwriting unless confirmed
- [ ] #2 Files above the attach limit can be read in parts over both transports
- [ ] #3 The default limits are reviewed against real client limits and changed or kept in a decision; HTTP never reaches the host's filesystem (decision-7 kept or superseded deliberately)
- [ ] #4 Security tests for symlinks, traversal, hard links, dot files, races and full disks; docs and live tests
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
