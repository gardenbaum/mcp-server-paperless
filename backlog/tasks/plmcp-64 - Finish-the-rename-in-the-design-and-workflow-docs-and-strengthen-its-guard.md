---
id: PLMCP-64
title: Finish the rename in the design and workflow docs and strengthen its guard
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T09:00:11Z'
updated_date: '2026-11-23T11:27:12Z'
labels: []
dependencies: []
type: chore
ordinal: 55000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The late independent review of PLMCP-59 (2026-11-23) found leftovers: doc-1 and doc-2 still name gardenbaum/paperless-mcp (including the PUT merge command), decisions 27, 28 and 32 name the old entrypoint and repository without a superseding decision, the guard in tests/quality/test_docs.py misses shields.io, raw.githubusercontent.com, SSH and API forms and does not cover the Dockerfile, the e2e HTTP test does not run the console script it claims to, docs/http.md does not connect the old command aliases with the PyPI name clash.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 No doc or Dockerfile names the old repository
- [x] #2 The guard catches shields.io, raw, SSH and API forms with a self-test
- [x] #3 A decision records the new entrypoint and Trusted Publisher repository
- [x] #4 The e2e HTTP test starts the mcp-server-paperless-http console script
- [x] #5 docs/http.md says not to run uvx paperless-mcp
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
1. Red: widen the guard in tests/quality/test_docs.py (every form of gardenbaum/paperless-mcp, self-test; scan README, docs/, root Markdown, doc-1/doc-2, Dockerfile); require the uvx warning in docs/http.md; mark decision-37 guards; break the console script to show the e2e HTTP test does not run it.
2. Green: doc-1/doc-2 through `backlog doc update`; docs/http.md; the e2e HTTP test starts the mcp-server-paperless-http console script.
3. Decision-37 supersedes decision-33 and decision-36 (which carry over 27, 28, 31 and 32): entrypoint names and Trusted Publisher repository change, the rest is carried over; update references to the superseded ids.
4. just check, e2e test_http.py under the lock, commit. Finalization is left to the controller.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: the guard self-test failed for 7 forms (shields.io plain and URL-encoded, raw, SSH, API, bare repos/ path, mixed case); on the docs of origin/main the new pattern finds 1 line in doc-1 and 2 in doc-2; a Dockerfile with the old URL fails the guard; the http.md warning test and the decision-37 marker check failed. e2e: with the mcp-server-paperless-http script moved out of .venv the old test_http.py still passed (2 passed), the new one fails on the missing console script. Green: test_http.py 2 passed against the E2E stack.
Decision change, deliberate: decision-37 supersedes decision-33 and decision-36 and carries both over unchanged except the entrypoint names and the Trusted Publisher repository (PyPI attestation of 0.3.0 names gardenbaum/mcp-server-paperless). References in docs/ point to decision-37. No snapshot, threshold or alias changed.
just check: lint, types, imports, deps and secrets pass; 4871 passed, 2 failed, both macOS-only (menu driver, socket path length; the second passes with TMPDIR=/tmp). Coverage 99.93 %; diff-cover does not run inside `just check` after a failed test, so it was run separately.
Trade-off for the owner: the title and file name of doc-1 stay "paperless-mcp design"; renaming breaks the path in CLAUDE.md, AGENTS.md, docs/ and about 35 tasks. Its heading in the body is renamed.

Review (opus): 0 Critical, 1 Important (decision-37 carried over decision-32's MCP server name, changed by PLMCP-59: now recorded), minors fixed: package-page form in the guard (red first), references in docs/development.md and image.yml, Enforcement wording. image.yml and pypi.yml still cite the decisions their rules came from. Left for the owner: doc-1's title and file name and config.yml project_name stay paperless-mcp. DoD 2 unchecked: just check has the one known macOS-only failure locally (1 failed, 4874 passed).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Docs and Dockerfile name only gardenbaum/mcp-server-paperless; the guard finds web, shields.io, raw, SSH, API, GHCR and package-page forms, with self-tests. decision-37 supersedes 33 and 36 and records the new entrypoints, Trusted Publisher repository and MCP server name. The e2e HTTP test starts the installed console script. docs/http.md warns against uvx paperless-mcp.
<!-- SECTION:FINAL_SUMMARY:END -->
