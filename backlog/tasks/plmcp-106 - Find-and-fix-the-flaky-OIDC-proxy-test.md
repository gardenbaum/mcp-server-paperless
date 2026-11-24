---
id: PLMCP-106
title: Find and fix the flaky OIDC proxy test
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T07:06:15Z'
updated_date: '2026-11-24T09:50:38Z'
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
- [x] #1 The cause is shown, and the test no longer depends on it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Show the cause: FastMCP keeps discovery documents for 300 s per process, keyed by URL; a stub issuer on a reused loopback port gets the document of an earlier one
2. Red: force the port reuse in a test
3. Green: every stub issuer gets its own URL path, so no cache key is shared; no product change
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Cause: FastMCP (oidc_proxy.py, get_oidc_configuration) caches discovery documents per process for 300 s, keyed by class, URL, strict and timeout. test_a_proxy_that_cannot_be_built_from_the_document_says_so leaves a document without token endpoint and with strict false in it; a later stub issuer that gets the same ephemeral port is answered from the cache, and OIDCProxy raises ValueError (Missing required OIDC endpoints). Both failing tests run after it.
Evidence: the port reuse forced in a test gives the message of the CI failure word for word (red); after the fix it passes (5 of 5 runs).
Not the cause: the discovery request (it has its own message and succeeded), the key derivation and secret checks (constant inputs), the stores (in memory, new per proxy); the RSA key is the only random value and is not read while the proxy is built.
Not shown: the CI log does not record that port 35909 had been used before.
Fix: every stub issuer has its own URL path, so a reused port never shares a cache key. This also covers the tests of left out fields and of the unreachable issuer. Product code unchanged: one configured provider per process.
The new test binds a port just released; if another process took it in between it fails with EADDRINUSE, not silently.
tests/test_oidc.py and tests/test_cli.py: 108 passed on 3.14.
<!-- SECTION:NOTES:END -->
