---
id: PLMCP-63
title: Publish the container image for linux/arm64 too
status: To Do
assignee: []
created_date: '2026-11-23T09:00:10Z'
labels: []
dependencies: []
type: feature
ordinal: 54000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The 0.3.0 image has no linux/arm64 manifest, so it does not start natively on a Raspberry Pi, ARM NAS or Apple Silicon. Many paperless-ngx users self-host on ARM. Owner approved on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A version tag publishes one tag that resolves for linux/amd64 and linux/arm64
- [ ] #2 Each architecture is smoke tested before the tag is published
- [ ] #3 Provenance and SBOM attestations cover both architectures
- [ ] #4 docs/container.md names the platforms
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
