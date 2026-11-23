---
id: PLMCP-101
title: Warn clearly when Paperless is older than the minimum release
status: To Do
assignee: []
created_date: '2026-11-23T20:43:01Z'
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
- [ ] #1 When Paperless reports a release below the minimum, the operator sees one log line and get_instance_overview says so, naming the found release, the minimum and that Paperless must be updated
- [ ] #2 An unknown, missing or unparsable version and a newer release give no false warning
- [ ] #3 The server still starts and works when Paperless is unreachable at start
- [ ] #4 The minimum is stated in one place in the code and tied to the documentation by a test
- [ ] #5 Unit, contract and E2E tests cover older, equal, newer and unknown versions; docs and snapshots are updated
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
