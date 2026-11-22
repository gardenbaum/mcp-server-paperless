---
id: PLMCP-53
title: Publish to PyPI as mcp-server-paperless
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-22T21:34:40Z'
updated_date: '2026-11-22T21:47:57Z'
labels: []
milestone: m-5
dependencies: []
priority: medium
type: chore
ordinal: 53000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Owner, 2026-11-22: publish the server to PyPI so clients run it with `uvx mcp-server-paperless@latest`, like npx-based MCP servers, instead of from a clone. The name `paperless-mcp` belongs to an unrelated PyPI project (tb1337/paperless-mcp), so `uvx paperless-mcp@latest` would hand a Paperless token to foreign code; `paperless-ngx-mcp` and `mcp-paperless-ngx` are taken too, and PyPI refuses names that differ from those only in separators. The owner registered a pending Trusted Publisher on PyPI: project `mcp-server-paperless`, repository gardenbaum/paperless-mcp, workflow `pypi.yml`, environment `pypi`. No API token is stored anywhere. The import package `paperless_mcp`, the MCP server name and the existing console scripts stay.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The distribution is named `mcp-server-paperless` and installs a console script of the same name that starts the stdio server, so `uvx mcp-server-paperless@latest` works; `paperless-mcp` and `paperless-mcp-http` keep working
- [ ] #2 `.github/workflows/pypi.yml` builds the sdist and wheel on pull requests that touch packaging and on `v*` tags, and on a tag publishes them to PyPI by Trusted Publishing from the `pypi` environment, only for a commit on main whose version equals the tag; only the publish job gets `id-token: write`, and every action is pinned to a commit
- [ ] #3 Guard tests keep the distribution name, the console script and the workflow rules from eroding, and a decision records the PyPI name and the publishing path
- [ ] #4 The README tells users to run the server with uvx from PyPI and warns that the PyPI project `paperless-mcp` is a different one
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
1. Decision-32 (CLI create, body in the file): PyPI name mcp-server-paperless and why (paperless-mcp, paperless-ngx-mcp, mcp-paperless-ngx taken; PyPI refuses separator-only variants); Trusted Publishing from pypi.yml and the pypi environment, v* tags only, commit on main, tag equals version, build once and publish those files with PEP 740 attestations, no API token, nobody publishes by hand.
2. Red first: tests/quality/test_pypi.py (marker decision-32) for the distribution name, the console script mcp-server-paperless next to paperless-mcp and paperless-mcp-http, __version__ from the renamed distribution, and the workflow rules (SHA pins, triggers, permissions, environment, ancestor and version checks, no token, no cache in the build).
3. pyproject name and script, __init__ metadata name, uv lock.
4. .github/workflows/pypi.yml: build job (uv build --no-sources, tag equals version, smoke-install the wheel: console script runs and refuses without configuration), publish job (tag-gated, environment pypi, id-token: write, ancestor check, download the built files, pypa/gh-action-pypi-publish).
5. README quick start and docs/http.md to uvx mcp-server-paperless@latest, warning about the PyPI project paperless-mcp.
6. just check, uv build plus a local wheel smoke test; PR. Version bump, tag and the pypi environment settings stay the owner's call.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first: the 9 tests in tests/quality/test_pypi.py failed for the right reasons (distribution name, missing metadata of mcp-server-paperless, missing pypi.yml), then passed.
uv_build derives the module name from the project name, so [tool.uv.build-backend] module-name = "paperless_mcp" keeps the import package until PLMCP-54 (owner, 2026-11-22: rename the package in its own PR before the first release).
The build job ran locally step by step: tag check passes for v0.1.0 and refuses v9.9.9; the wheel installs alone, reports 0.1.0, and mcp-server-paperless refuses with "invalid configuration" without settings.
just check: lock, lint, format, basedpyright 0/0/0, 9 contracts kept, deptry, gitleaks green; 4632 tests passed, coverage 99.93 %, diff coverage 100 %. Two tests fail on this macOS machine also on unchanged main 052dffa (test_a_socket_is_not_a_file: AF_UNIX path too long; test_the_menu_driver_reports_a_failing_cli: EIO writing to the pty after the child exited), unrelated to this change; CI runs on Linux.
No live e2e run locally: the shared Paperless stack may be in use by a concurrent session; the stdio e2e test keeps using the unchanged paperless-mcp script, and the PR e2e tier runs it.
The publish job cannot be verified before the first version tag (owner).
<!-- SECTION:NOTES:END -->
