---
id: PLMCP-54
title: Rename the import package to mcp_server_paperless
status: To Do
assignee:
  - '@claude'
created_date: '2026-11-22T21:39:19Z'
updated_date: '2026-11-22T23:07:45Z'
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
- [ ] #1 The import package is `mcp_server_paperless` everywhere in src, tests, scripts and configuration, and the uv_build `module-name` override is gone
- [ ] #2 The import-linter contracts, the coverage and mutmut configuration and the recorded mutant equivalents name the new package and still pass unchanged in strength
- [ ] #3 README and docs name the new module (`python -m mcp_server_paperless`) and the changed logger names; the console scripts keep their names
- [ ] #4 This is the release pull request: it sets the first PyPI version, switches README and docs/http.md to `uvx mcp-server-paperless@latest` with a warning that the PyPI project `paperless-mcp` is a different server, and its `v*` tag is pushed right after the merge so the name is claimed within minutes
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Deferred from the PLMCP-53 review (Minor): the README's relative docs/ links break on the PyPI project page, which renders README.md; consider absolute links or a PyPI-specific readme when switching the quick start. Run as mcp-server-paperless, error messages still start with "paperless-mcp:".
<!-- SECTION:NOTES:END -->
