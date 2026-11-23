---
id: decision-37
title: >-
  HTTP entrypoint mcp-server-paperless-http and Trusted Publisher
  gardenbaum/mcp-server-paperless
date: '2026-11-23T11:11:38Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The owner renamed the GitHub repository from gardenbaum/paperless-mcp to gardenbaum/mcp-server-paperless (2026-11-23), and PLMCP-59 moved the image and the docs to the console scripts `mcp-server-paperless` and `mcp-server-paperless-http`. Accepted decisions still named the old ones: decision-27 and decision-28 (carried over by decision-31 and decision-36) the entrypoint `paperless-mcp-http` from `paperless_mcp.cli`, and decision-32 (carried over by decision-33) the Trusted Publisher repository gardenbaum/paperless-mcp. The attestation of the release 0.3.0 on PyPI names the publisher gardenbaum/mcp-server-paperless with the workflow `pypi.yml` and the environment `pypi`.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes decision-33 and decision-36.** Everything in both, and what they carry over from decision-32 and from decision-27, decision-28 and decision-31, is carried over unchanged (including its consequences and enforcement), except exactly these names:

- Decision-27's entrypoint `paperless-mcp-http` (`paperless_mcp.cli:main_http`) becomes `mcp-server-paperless-http` (`mcp_server_paperless.cli:main_http`), and its stdio command `paperless-mcp` becomes `mcp-server-paperless` (`mcp_server_paperless.cli:main`).
- Decision-28's image entrypoint `paperless-mcp-http` becomes `mcp-server-paperless-http`.
- Decision-32's "the console scripts `paperless-mcp` and `paperless-mcp-http` stay" still holds: they are aliases of the two scripts above, kept so existing configurations keep working. The docs do not recommend them and warn against `uvx paperless-mcp`, which would run an unrelated PyPI project.
- Decision-32's Trusted Publisher repository gardenbaum/paperless-mcp becomes gardenbaum/mcp-server-paperless; workflow `pypi.yml` and environment `pypi` are unchanged.
- Decision-32's "the MCP server name … do not change" no longer holds for the name: PLMCP-59 changed it from `paperless-mcp` to `mcp-server-paperless`. The environment variables still do not change.
- Current docs and the Dockerfile name only gardenbaum/mcp-server-paperless, as repository and as image. Decisions and tasks keep the names of their time.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A test guards the repository name in the docs, not the setting on PyPI: the Trusted Publisher is the owner's account setting, and a release fails if it names another repository.
- This decision changes nothing at run time; it records names that PLMCP-59 changed. No console script, variable or image name is added or removed.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_docs.py` (marker decision-37): no root page, page in docs/, Backlog doc or the Dockerfile names the old repository or image as owner and name (web, SSH, API, raw, badge, GHCR, package page); self-tests show the forms the guard finds, the mentions it leaves alone and the files it covers.
- `tests/quality/test_pypi.py` (marker decision-37): the console scripts and the repository URL in pyproject.toml, and the warning against `uvx paperless-mcp` in docs/http.md.
- `tests/quality/test_container.py` (marker decision-37): the image entrypoint.
- `tests/e2e/test_http.py` (marker decision-37): the installed `mcp-server-paperless-http` script serves the MCP endpoint against a real Paperless.
- The Trusted Publisher's repository on PyPI is checked manually by the owner (manual review); the attestation of each release names it.
- Everything decision-33 and decision-36 list under Enforcement still runs unchanged.
