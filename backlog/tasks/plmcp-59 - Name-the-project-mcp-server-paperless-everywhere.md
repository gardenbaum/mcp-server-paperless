---
id: PLMCP-59
title: Name the project mcp-server-paperless everywhere
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T00:52:44Z'
updated_date: '2026-11-23T01:07:48Z'
labels: []
dependencies: []
type: chore
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The owner renamed the GitHub repository from gardenbaum/paperless-mcp to gardenbaum/mcp-server-paperless (2026-11-23) and wants the project called mcp-server-paperless everywhere from now on, matching the PyPI distribution (decision-32). The old name also collides with three other paperless-ngx MCP servers called paperless-mcp and with the unrelated PyPI project paperless-mcp. Out of scope, because renaming them breaks every existing configuration or rewrites history: the PAPERLESS_MCP_* environment variables, the PLMCP task prefix, the Python import package (already mcp_server_paperless) and accepted decision bodies and archived tasks. The PyPI Trusted Publisher still names the repository paperless-mcp and must be updated on PyPI by the owner before the next release.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 README, docs, pyproject URLs, AGENTS.md/CLAUDE.md, the server's MCP name and the examples call the project mcp-server-paperless and link to github.com/gardenbaum/mcp-server-paperless
- [x] #2 The container image is published as ghcr.io/gardenbaum/mcp-server-paperless; the docs say where the old image name stays
- [x] #3 The PAPERLESS_MCP_* variables keep working unchanged
- [x] #4 A docs test fails when a README or docs link points to the old repository name
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
1. Docs test: no README/docs page names the old repository or image (one allowed note on the image kept up to 0.2.0).
2. Rewrite repository URLs in README, docs, pyproject, Dockerfile.
3. Server MCP name, CLI error prefix, Dockerfile title/user/entrypoint, new mcp-server-paperless-http script; old script names stay as aliases.
4. Image name follows github.repository, so ghcr.io/gardenbaum/mcp-server-paperless from the next tag; docs pin 0.3.0 and name the old image.
5. Keep PAPERLESS_MCP_* variables, OIDC storage salt, /var/lib/paperless-mcp, e2e compose project names, decision bodies and archived tasks.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence: just check green except tests/quality/test_e2e_ci.py::test_the_union_of_the_shards..., which fails only with the local PYTEST_ADDOPTS deselect (its -k leaks into the subprocess collection) and passes without it. e2e test_stdio.py + test_http.py: 5 passed against the E2E stack (new console script mcp-server-paperless).
Kept for compatibility: PAPERLESS_MCP_* variables, console scripts paperless-mcp/paperless-mcp-http, OIDC storage salt, /var/lib/paperless-mcp in the image, uid 10001.
Owner action before the next release: PyPI Trusted Publisher still names repository paperless-mcp; add/update it to gardenbaum/mcp-server-paperless.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Called the project mcp-server-paperless everywhere users see it: README, docs, pyproject URLs, AGENTS.md/CLAUDE.md, the MCP server name, CLI messages, the image title and entrypoint (new mcp-server-paperless-http script). The image is published as ghcr.io/gardenbaum/mcp-server-paperless from the next tag (it follows github.repository); docs name the old image for 0.1.0-0.2.0. The PAPERLESS_MCP_* variables and the old console script names keep working. A new docs test fails on links to the old repository or image name. Risk: the PyPI Trusted Publisher must be updated to the renamed repository before the next release.
<!-- SECTION:FINAL_SUMMARY:END -->
