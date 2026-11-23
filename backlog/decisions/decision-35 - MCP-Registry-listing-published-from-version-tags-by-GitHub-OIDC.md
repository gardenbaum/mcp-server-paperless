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
- `.github/workflows/registry.yml`, separate from ci.yml and pypi.yml and never a required check, runs only on a pushed `v*` tag. Its `prepare` job, without an OIDC token, requires the tagged commit to be an ancestor of origin/main, takes the version from the tag in the PEP 440 form pypi.yml publishes (`uv version --frozen -- "${TAG#v}"`, decision-33) and waits up to 15 minutes until PyPI serves that version, failing otherwise. The `publish` job needs `prepare` and has no `if`, so a failed check skips it; it runs no uv. It writes the version into the listing and its package, downloads the pinned mcp-publisher release, verifies it with cosign against the registry's release workflow at that tag, logs in with `mcp-publisher login github-oidc` and publishes.
- The pypi.yml hardening applies: `contents: read` by default, `id-token: write` only on the `publish` job, every action pinned to a commit SHA with a version comment, `persist-credentials: false`, setup-uv without a cache, values reach shell only through `env`, no `continue-on-error`, no `pull_request_target` or `workflow_run`. mcp-publisher is raised deliberately, never fetched from a moving latest URL.
- The publish job uses no GitHub environment, and the repository has no tag ruleset: whoever may push a `v*` tag on a commit on main can publish a listing. A tag ruleset limiting who may create `v*` tags is recommended to the owner, as in decision-32; the repository does not set it.
- `glama.json` names gardenbaum as maintainer so Glama can tie its listing to the owner.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A version tag now publishes three things: the image, the PyPI release and the registry listing. A tag whose PyPI release fails lists nothing.
- No registry token exists; the namespace comes from the repository owner in GitHub's OIDC token.
- Whether the job works is not verified until the next version tag (the owner's call). v0.3.0 predates the workflow and is not listed by it.
- A pre-release is listed in its PEP 440 form (`1.2.3rc1`), which is no semantic version: the registry (its code at v1.8.15) sorts it below any semantic version, and the first published version is the latest whatever its form.
- A rerun after a successful publish fails harmlessly: the registry answers that the version already exists.
- The MCP Registry is in preview; a schema change shows up as a failed job and a new schema URL in server.json.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_registry.py` (marker decision-35): the listing's name equals the README marker, its schema URL, description length and repository; the package is the pyproject.toml distribution over stdio with `uvx`, and no other key (package index, remotes) exists; the declared variables are exactly those PaperlessSettings and ServerSettings read, with their required flags, string defaults and log level choices, and only the token is secret; glama.json; the workflow's exact trigger, keys, jobs and steps with the scripts word for word, so an added `if`, `continue-on-error`, `ref` or `|| true` fails; pinned actions; no expression in a script; `id-token` only in `publish`.
- `mcp-publisher validate` against the registry, run locally when server.json changes.
