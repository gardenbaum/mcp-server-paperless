---
id: PLMCP-101
title: Warn clearly when Paperless is older than the minimum release
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T20:43:01Z'
updated_date: '2026-11-24T10:08:28Z'
labels:
  - tools
  - config
dependencies: []
priority: medium
type: enhancement
ordinal: 77000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The minimum is paperless-ngx 3.5.1 (decision-43), but the server never compares the version: API version 10 also matches 3.4, so an older Paperless is accepted and fails later with unexpected answers, for example queued operations without a task id. X-Version is only shown by get_instance_overview. Approved by the owner on 2026-11-23 as a warning.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 When Paperless reports a release below the minimum, the operator sees one log line and get_instance_overview says so, naming the found release, the minimum and that Paperless must be updated
- [x] #2 An unknown, missing or unparsable version and a newer release give no false warning
- [x] #3 The server still starts and works when Paperless is unreachable at start
- [x] #4 The minimum is stated in one place in the code and tied to the documentation by a test
- [x] #5 Unit, contract and E2E tests cover older, equal, newer and unknown versions; docs and snapshots are updated
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Tests first (domain, adapter, server/tool, contract, quality). 2. domain/instance: MINIMUM_RELEASE and update_notice (X.Y.Z only; anything else gives none). 3. PaperlessClient logs the notice once, on any response with X-Version, so also when Paperless was unreachable at start. 4. get_instance_overview carries the notice in a field of its own, too_old (DEFINITION_ADDED_LIMIT 750 to 854, approved by the owner). 5. A missing X-Version reads as unknown. 6. E2E on rewritten headers, docs, decision-57.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner question (2026-11-24): the notice was first in paperless_version, for lack of budget; on the coordinator's instruction it is now a field of its own, too_old. DEFINITION_ADDED_LIMIT 750 -> 854 (measured 747 -> 854, 107 bytes), approved by the owner on 2026-11-24 (own field, as little as possible) before the number was known. Snapshot test_surface.ambr: the new field and its place in required, in each profile that has the overview; nothing else. Choices: only X.Y.Z is compared; a missing X-Version reads as unknown (before, the overview and the start failed); the log line comes from PaperlessClient, once per start, so also after an unreachable start. decision-57 supersedes the sentence of decision-43 that the version is not checked. No other limit or gate changed. Live tests are written but were not run locally.

Review round (2026-11-24): X-Version counts only with X-Api-Version beside it; the 406 hint names the minimum (no snapshot or doc contains it); an empty X-Version is unknown too; clearer description of too_old. DEFINITION_ADDED_LIMIT is now 879 (instead of the 854 above; measured 879, so 132 bytes of too_old), within the owner's approval. Full local suite on 20ce23c: the two known macOS failures only (menu driver, socket path), diff-cover 100 %; later only targeted tests, for lack of memory.

Merged with #123 (4ece42a). Review and delta review clean, Codex without findings. Hosted full E2E 45357325758 green on d849820 (tree of the merged head), shard 3 on its second attempt: the first hit the 25-minute guard at 97 %, see PLMCP-44. DoD 2 stays unchecked (macOS-only failures).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A Paperless older than 3.5.1 gives one warning line per start and a notice in the new field too_old of get_instance_overview (decision-57); unknown, newer and unparsable versions give none. DEFINITION_ADDED_LIMIT 750 to 879, approved by the owner.
<!-- SECTION:FINAL_SUMMARY:END -->
