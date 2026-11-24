---
id: PLMCP-106
title: Find and fix the flaky OIDC proxy test
status: To Do
assignee: []
created_date: '2026-11-24T07:06:15Z'
labels: []
dependencies: []
priority: low
ordinal: 81000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
tests/test_oidc.py::test_an_mcp_path_that_collides_with_an_oauth_route_does_not_raise_its_limit failed once on Python 3.15 in CI (2026-11-23, 0b8ef1b): ValueError while the OIDC proxy was set up from a loopback discovery document; green on the next runs. Guess, not proven: FastMCP caches discovery documents by URL and an ephemeral port was reused.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The cause is shown, and the test no longer depends on it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
