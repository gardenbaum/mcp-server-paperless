---
id: PLMCP-117
title: >-
  Bound what comes after the end of a compressed stream in the other streamed
  reads
status: To Do
assignee: []
created_date: '2026-11-24T21:58:47Z'
labels:
  - safety
dependencies: []
priority: low
type: bug
ordinal: 98000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
httpx2's gzip and deflate decoders keep data after the end of the stream and copy it with every piece, and do not count empty blocks (PLMCP-111: 67 MB on the wire took 2.6 GB in a JSON read). list_trash and the error bodies of bounded JSON reads are bounded since PLMCP-111; streamed downloads (download, stream, stream_post) and their error bodies still use those decoders. Read in the code, not probed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Streamed downloads and the error bodies of every streamed read take bounded memory whatever follows the end of a compressed stream, shown by the memory of a process
- [ ] #2 Files that Paperless sends, also compressed, are read as before
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
