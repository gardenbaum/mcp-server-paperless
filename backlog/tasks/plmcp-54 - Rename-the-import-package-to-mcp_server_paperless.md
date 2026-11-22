---
id: PLMCP-54
title: Rename the import package to mcp_server_paperless
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-22T21:39:19Z'
updated_date: '2026-11-22T23:49:08Z'
labels: []
milestone: m-5
dependencies:
  - PLMCP-53
priority: medium
type: chore
ordinal: 54000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Owner, 2026-11-22: after PLMCP-53 the distribution and its console script are `mcp-server-paperless`, but the import package is still `paperless_mcp`, kept there by a `module-name` override for uv_build. The unrelated PyPI project `paperless-mcp` (tb1337) installs a top-level module `paperless_mcp` and a `paperless-mcp` console script too, so both packages in one environment would overwrite each other. Rename the package before the first PyPI release, while nobody imports it yet, as one purely mechanical pull request (about 1,130 occurrences in about 250 files, including the import-linter contracts, the mutmut configuration and the recorded mutant equivalents). Logger names and `python -m` change with it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The import package is `mcp_server_paperless` everywhere in src, tests, scripts and configuration, and the uv_build `module-name` override is gone
- [x] #2 The import-linter contracts, the coverage and mutmut configuration and the recorded mutant equivalents name the new package and still pass unchanged in strength
- [x] #3 README and docs name the new module (`python -m mcp_server_paperless`) and the changed logger names; the console scripts keep their names
- [ ] #4 This is the release pull request: it switches README and docs/http.md to `uvx mcp-server-paperless@latest` with a warning that the PyPI project `paperless-mcp` is a different server, and a new `v*` tag (above v0.1.1, whose image is already published; the version comes from the tag, decision-33) is pushed right after the merge so the name is claimed within minutes
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
1. Red first: tests/quality/test_pypi.py expects the console scripts at mcp_server_paperless.cli, no uv_build module-name override, the smoke test importing mcp_server_paperless, and README plus docs/http.md starting the server with uvx mcp-server-paperless@latest beside a warning about the unrelated PyPI project paperless-mcp.
2. git mv src/paperless_mcp src/mcp_server_paperless; replace the module name in src, tests, scripts, justfile, pyproject.toml (import-linter, coverage, mutmut), mutation-equivalents.toml and pypi.yml; drop module-name. Decisions and task records stay as written (history).
3. README and docs/http.md: uvx mcp-server-paperless@latest, warning, and absolute links in README so they work on the PyPI page (deferred PLMCP-53 review note).
4. just check, lint-imports, a wheel build with python -m mcp_server_paperless; PR; after merge the owner pushes or approves a new v* tag above v0.1.1.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Deferred from the PLMCP-53 review (Minor): the README's relative docs/ links break on the PyPI project page, which renders README.md; consider absolute links or a PyPI-specific readme when switching the quick start. Run as mcp-server-paperless, error messages still start with "paperless-mcp:".

Red first: tests/quality/test_pypi.py failed at collection (import mcp_server_paperless), then the new package test (src/mcp_server_paperless, no src/paperless_mcp, no [tool.uv.build-backend]) and the docs test (README and docs/http.md name mcp-server-paperless@latest, no git+https, link https://pypi.org/project/paperless-mcp/) failed until the rename and the docs switch.
Rename: git mv src/paperless_mcp src/mcp_server_paperless and a word replacement outside backlog/ and docs/vendor/. Two hits were the lowercased environment variable prefix, not the module (tests/test_settings.py test name, tests/quality/test_container.py PAPERLESS_MCP_HTTP_PORT), and were restored. Decisions and task records keep the old name as history. ruff re-sorted imports and three lines were rewrapped.
README links repository files by their https://github.com/gardenbaum/paperless-mcp/blob/main/ URL so they work on the PyPI page (deferred PLMCP-53 review note); tests/quality/test_docs.py checks those links like relative ones (file and heading) and a new test refuses relative file links in the README.
Docs never named python -m or the logger names, so AC #3 needs no doc text beyond the package name; the logger names follow __name__ and are now mcp_server_paperless.*.
Verified: just check green (4639 passed, the two known macOS-only tests deselected; 9 contracts kept; basedpyright 0/0/0); wheel from uv build --no-sources holds only mcp_server_paperless, python -m mcp_server_paperless and mcp-server-paperless start and refuse without configuration; a mutmut run on mcp_server_paperless.paperless.pagination* names its mutants mcp_server_paperless.paperless.pagination.x_collect__mutmut_16/17, matching mutation-equivalents.toml.
<!-- SECTION:NOTES:END -->
