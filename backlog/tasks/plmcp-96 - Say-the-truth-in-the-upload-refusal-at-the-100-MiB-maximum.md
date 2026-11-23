---
id: PLMCP-96
title: Say the truth in the upload refusal at the 100 MiB maximum
status: To Do
assignee: []
created_date: '2026-11-23T15:33:41Z'
labels: []
dependencies: []
priority: low
type: bug
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
services/upload_source.py tells the caller that the operator can raise PAPERLESS_MCP_MAX_UPLOAD_BYTES. At the setting's maximum of 100 MiB that is untrue. Found in the review of PLMCP-95; the owner approved a separate task after 0.3.3 on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A refused upload never says that the limit can be raised when it is already 100 MiB
- [ ] #2 Below the maximum the refusal still names the setting the operator can raise
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
