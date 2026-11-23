---
id: decision-35
title: MCP Registry listing published from version tags by GitHub OIDC
date: '2026-11-23T08:28:27Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The server is on PyPI as mcp-server-paperless (decision-33), but MCP clients and aggregators such as Glama find servers through the official MCP Registry (registry.modelcontextprotocol.io). The registry stores only metadata, server.json, and accepts a PyPI package only when PyPI serves the version and the package README carries `mcp-name: <server name>`. Listing by hand would drift from the releases. Decision-32 (as carried over by decision-33) set the rules for a channel that publishes from a tag; a new channel that holds an OIDC token needs its own.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- The listing is `server.json` at the repository root, named `io.github.gardenbaum/mcp-server-paperless` like the README marker, with one PyPI package `mcp-server-paperless` run by `uvx` over stdio and the environment variables of the stdio server (PAPERLESS_URL and PAPERLESS_TOKEN required, the token secret). Its version is a placeholder.
- `.github/workflows/registry.yml`, separate from ci.yml and pypi.yml and never a required check, runs only on a pushed `v*` tag. Its one job requires the tagged commit to be an ancestor of origin/main, sets the version of the listing and its package from the tag in the PEP 440 form pypi.yml publishes (`uv version --frozen -- "${TAG#v}"`, decision-33), waits up to 15 minutes until PyPI serves that version and fails otherwise, downloads the pinned mcp-publisher release, verifies it with cosign against the registry's release workflow at that tag, logs in with `mcp-publisher login github-oidc` and publishes.
- The pypi.yml hardening applies: `contents: read` by default, `id-token: write` only on the job, every action pinned to a commit SHA with a version comment, `persist-credentials: false`, setup-uv without a cache, values reach shell only through `env`, no `continue-on-error`, no `pull_request_target` or `workflow_run`. mcp-publisher is raised deliberately, never fetched from a moving latest URL.
- `glama.json` names gardenbaum as maintainer so Glama can tie its listing to the owner.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A version tag now publishes three things: the image, the PyPI release and the registry listing. A tag whose PyPI release fails lists nothing.
- No registry token exists; the namespace comes from the repository owner in GitHub's OIDC token.
- Whether the job works is not verified until the next version tag (the owner's call). v0.3.0 predates the workflow and is not listed by it.
- The MCP Registry is in preview; a schema change shows up as a failed job and a new schema URL in server.json.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_registry.py` (marker decision-35): the listing's name equals the README marker, its schema URL, description length and repository; the package is the pyproject.toml distribution over stdio with `uvx`; every declared variable is one the settings read, the required ones match, only the token is secret; glama.json; the workflow's trigger, permissions, pins, no expression in a script, no `continue-on-error`, the main ancestry check, the version from the tag (marker decision-33), the PyPI wait before the login, and the cosign-verified pinned mcp-publisher before `login github-oidc` and `publish`.
- `mcp-publisher validate` against the registry, run locally when server.json changes.
