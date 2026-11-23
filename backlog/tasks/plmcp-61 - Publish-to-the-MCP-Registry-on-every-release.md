---
id: PLMCP-61
title: Publish to the MCP Registry on every release
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T08:26:24Z'
updated_date: '2026-11-23T09:21:59Z'
labels: []
dependencies: []
type: chore
ordinal: 58000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The server is on PyPI (decision-33) but not in the official MCP Registry (registry.modelcontextprotocol.io), which MCP clients and aggregators such as Glama read to find servers. Publishing by hand would drift from the PyPI releases; a version tag should list the same version in the registry, and Glama should be able to tie its listing to the maintainer.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 server.json at the repository root validates against the registry schema it names, with the name io.github.gardenbaum/mcp-server-paperless, the PyPI package mcp-server-paperless over stdio and the environment variables the stdio server reads
- [x] #2 A separate workflow publishes server.json to the MCP Registry on every v* tag by GitHub OIDC, only for a commit on main, only after PyPI serves that version, with the version taken from the tag
- [x] #3 The workflow follows the hardening of pypi.yml: actions pinned to commits with version comments, a pinned mcp-publisher verified with cosign, id-token: write only on the job, no expression in a script, no continue-on-error
- [x] #4 glama.json names gardenbaum as maintainer
- [x] #5 Quality tests guard the name against the README marker, the package against pyproject.toml, the variables against the settings, glama.json and the workflow rules
- [x] #6 docs/development.md names the registry channel in its Releases section
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Tests first in tests/quality/test_registry.py (server.json name vs README marker, package vs pyproject, env vars vs settings, glama.json, workflow hardening and steps); run red.
2. server.json (schema 2025-12-11, pypi/stdio/uvx, env vars), glama.json, .github/workflows/registry.yml (main ancestry, wait for PyPI, jq version from tag, mcp-publisher v1.8.15 verified with cosign, login github-oidc, publish).
3. decision-35 for the new channel; docs/development.md Releases.
4. mcp-publisher validate locally, just check, PR.

5. Independent review fixes: split the job so uv never runs beside the OIDC token; exact-match tests.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- tests/quality/test_registry.py ran red (10 failed, files missing), then green (10 passed).
- The tests caught all 12 weakened copies of registry.yml tried (ancestor step renamed or neutered, persist-credentials, cache, continue-on-error, an expression in a script, the wait loop exit, a non-semver mcp-publisher pin, the cosign issuer flag, a workflow-level id-token, an unpinned action, the package version left out of the jq filter).
- `mcp-publisher validate` (Homebrew 1.8.15) reports server.json as valid against registry.modelcontextprotocol.io, both with the 0.0.0 placeholder and with 0.3.0.
- mcp-publisher pinned to v1.8.15, the latest release (2026-11-22); sigstore/cosign-installer v4.1.2 at the SHA the registry guide gives, checked against the tag via the GitHub API.
- decision-35 records the new channel (decision-32/33 cover only PyPI and say every tag-published channel needs its rules).
- just check: every recipe passes except two tests that also fail on an unchanged main checkout in this macOS sandbox: test_a_socket_is_not_a_file (AF_UNIX path too long under the sandbox TMPDIR; passes with TMPDIR=/tmp) and test_the_menu_driver_reports_a_failing_cli (pty Input/output error). Neither touches this change; CI runs them on Linux.
- DoD #2 (CI green) and #4 (merged) are left unchecked: they become true only after this commit, on the PR.

- Correction: the first commit declared only 7 of the 11 variables the stdio server reads, so AC 1 was checked too early. All 11 are declared now and the test requires exactly that set (run red first).
- Correction: the mcp-publisher download now detects os/arch exactly as the registry guide does; it was hardcoded to linux_amd64.
- Correction: decision-32/33 do not say every channel needs its own rules; decision-35 was written because a second job now holds an OIDC token.
- The causes named above for the two local failures were inferred from their error messages. The pty failure was not investigated; both tests pass in CI (Linux).
- The cosign verification was not run locally (no cosign). The signing certificate of the v1.8.15 bundle names the expected identity and issuer.

- Independent opus review of b1ca557: no Critical, two Important, several Minor.
- I1 fixed: the tests compare jobs, steps and scripts word for word, plus defaults, choices and keys of server.json. 39 weakenings on a scratch copy, 0 survivors. This replaces the earlier claim about 12 weakenings, which were too easy: the review found 21 of 22 survivors.
- I2 fixed: a prepare job without OIDC token holds the main check, the uv version step and the PyPI wait; publish needs it and runs no uv. The new tests ran red against the old workflow (6 failed), then green. The wait is now bounded by elapsed time, since --max-time on a counted loop could have exceeded 15 minutes.
- M1, M2, M3 in decision-35 and the workflow. M2 checked against internal/service/versioning.go at v1.8.15; the registry documentation describes it differently. M4 is PLMCP-65.
- The reviewer ran the cosign verification against the real v1.8.15 assets and it passed (as reported by the coordinator).
- just check on the final code: every recipe ok except test_the_menu_driver_reports_a_failing_cli, which fails on this Mac with a pty error (not investigated) and passed in CI on the previous head. The earlier wording that it fails on an unchanged main is unproven.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Publishes the server to the official MCP Registry on every version tag (decision-35). server.json lists io.github.gardenbaum/mcp-server-paperless: the PyPI package over stdio with uvx and all eleven environment variables of the stdio server. registry.yml runs on v* tags only: prepare (no OIDC token) requires the tagged commit on main, takes the PEP 440 version from the tag and waits up to 15 minutes for PyPI; publish (needs prepare, the only id-token) writes the version, downloads mcp-publisher v1.8.15 verified by cosign, logs in by GitHub OIDC and publishes. glama.json names gardenbaum. tests/quality/test_registry.py matches the workflow word for word (39 weakenings, none survive).

Risk: first exercised by the next version tag; v0.3.0 is not listed by it. No tag ruleset or environment protects the publish job (recommended to the owner). Follow-up: PLMCP-65.
<!-- SECTION:FINAL_SUMMARY:END -->
