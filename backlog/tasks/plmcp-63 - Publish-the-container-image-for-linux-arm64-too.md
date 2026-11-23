---
id: PLMCP-63
title: Publish the container image for linux/arm64 too
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T09:00:10Z'
updated_date: '2026-11-23T09:47:41Z'
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
- [x] #2 Each architecture is smoke tested before the tag is published
- [ ] #3 Provenance and SBOM attestations cover both architectures
- [x] #4 docs/container.md names the platforms
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
1. Guards first (tests/quality/test_container.py). 2. image.yml: one native runner per platform for build and publish; a tag job merges the scanned digests. 3. Decision-36 and docs. 4. just check, opus review, Codex, CI builds both platforms.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
decision-36 supersedes decision-31 (carry-over). Guard changes deliberate: tagging moved to the tag job. arm64 image built and smoke-tested locally on Apple Silicon.

Opus review: Critical (Trivy scans linux/amd64 of an arm64-only index) fixed with TRIVY_PLATFORM, also Codex P1; guards now include a snapshot of the whole workflow (new snapshot, deliberate), 10 tried weakenings caught. Stays In Progress: AC 1 and 3 can only be proven by the 0.3.1 tag.
<!-- SECTION:NOTES:END -->
