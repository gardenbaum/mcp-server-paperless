---
superseded_by: decision-33
updated_date: '2026-11-22T23:21:22Z'
id: decision-32
title: >-
  PyPI distribution: mcp-server-paperless, published from version tags by
  Trusted Publishing
date: '2026-11-22T21:36:20Z'
status: superseded
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->

The owner (2026-11-22) wants clients to start the server with `uvx <name>@latest`, the way npx-based MCP servers run, instead of from a clone or a Git URL. That needs a PyPI release. The project's own name is not available there: `paperless-mcp` is an unrelated server (tb1337/paperless-mcp, which also installs a top-level module `paperless_mcp` and a `paperless-mcp` console script), and `paperless-ngx-mcp` and `mcp-paperless-ngx` are taken too. PyPI refuses a new name that differs from an existing one only in separators, so `paperlessngx-mcp` or `paperlessmcp` are not available either. Running `uvx paperless-mcp@latest` would hand the user's Paperless token to that foreign package. A PyPI release, like the container image (decision-28 as carried over by decision-31), cannot be taken back once published: a version number is never reusable.

<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->

- The distribution is `mcp-server-paperless`, after the naming of the reference MCP servers (`mcp-server-git`, `mcp-server-fetch`). It installs a console script of the same name that starts the stdio server, so `uvx mcp-server-paperless@latest` works; the console scripts `paperless-mcp` and `paperless-mcp-http` stay. Until PLMCP-54 renames the import package to `mcp_server_paperless` (before the first PyPI release), uv_build's `module-name` keeps it at `paperless_mcp`. The MCP server name and the environment variables do not change. `[project.urls]` names the repository, which PyPI shows as verified because it is the Trusted Publisher's.
- Publishing is Trusted Publishing only: PyPI trusts the workflow `.github/workflows/pypi.yml` of gardenbaum/paperless-mcp in the GitHub environment `pypi` (registered by the owner as a pending publisher). No PyPI API token exists, in the repository or anywhere else.
- `pypi.yml` is separate from ci.yml, so it can never be a required check or slow a merge. Pull requests that touch the package (pyproject.toml, uv.lock, README.md, LICENSE, src, the workflow) and version tags run the `build` job: `uv build --no-sources`, then a smoke test of the wheel alone in a fresh environment (it installs, reports its version, and `mcp-server-paperless` starts and refuses to run without configuration), then the files go up as an artifact. On a tag the job first requires the tag to be `v` followed by the version exactly as `uv version --short` prints it, the PEP 440 normal form (`v1.2.3`, `v1.2.3rc1`). setup-uv runs without a cache, so nothing a pull request wrote can feed a release build. No job or step may `continue-on-error`, and the two checks have no condition beyond the tag.
- Only a pushed `v*` tag runs the `publish` job, the only job with `id-token: write` (the workflow's default is `contents: read`). It runs in the `pypi` environment, requires the tagged commit to be an ancestor of origin/main, downloads the files the `build` job built and uploads exactly those with `pypa/gh-action-pypi-publish`, which attaches PEP 740 attestations by default. No `pull_request_target`, no `workflow_run`, every action pinned to a commit SHA with a version comment, `persist-credentials: false`, untrusted values reach shell only through `env`.
- Deployment protection on the `pypi` environment (only `v*` tags may deploy, optionally a required reviewer) and a tag ruleset limiting who may create `v*` tags are recommended repository settings, not set by the repository.
- Nobody publishes by hand. Pushing a version tag is the owner's decision; one tag publishes the image and the PyPI release together when it names a plain version (`v1.2.3`), the form both workflows read alike. A semver pre-release tag such as `v1.2.3-rc.1` never equals the PEP 440 form `1.2.3rc1`: it would publish the image but no PyPI release.

<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->

- A release needs the version in pyproject.toml set before the tag (`uv version X.Y.Z`); a tag that names another version publishes nothing to PyPI. The existing tag v0.1.0 predates this workflow, so the first PyPI release is a new version.
- `@latest` means the newest PyPI release, not main: a merge alone changes nothing for uvx users.
- The README and docs/http.md keep the Git URL command until the first release: the pending publisher reserves nothing, so pointing users at `mcp-server-paperless` before its first upload would hand their Paperless token to whoever registers the name first. The release pull request (PLMCP-54) switches them to `uvx mcp-server-paperless@latest`, with a warning that the PyPI project `paperless-mcp` is a different server, and its tag is pushed right after the merge so the name is taken within minutes.
- Whether the publish job works is not verified until the first version tag is pushed (manual, the owner's call); the pending publisher turns into the project on that first upload, and the name is not reserved before it.

<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_pypi.py` (marker decision-32): the distribution name, the console script of the same name beside `paperless-mcp` and `paperless-mcp-http` and the repository URL; the version read from the renamed distribution; for every step of every job, a SHA pin with a semver comment, no `continue-on-error` and no expression inside a script; exactly the jobs `build` and `publish`; triggers limited to `v*` tags and pull requests to main with the package paths; `id-token: write` exactly once, in the publish job, gated by the exact tag condition and in the `pypi` environment; the ancestor check (found by name, unconditional, `TAGGED_SHA` from the environment, full history, before the upload) and the tag-equals-version check (found by name, only its tag condition, before the build); the publish job's steps exactly checkout, ancestor check, download of the artifact the build uploaded, and the PyPA action without a password or other inputs that would turn attestations off; `uv build --no-sources`; no `pull_request_target` or `workflow_run`, `persist-credentials: false` on both checkouts and setup-uv without a cache; the wheel smoke test between build and upload; separation from ci.yml. The workflow is parsed with PyYAML. These tests were checked against nine weakened copies of the workflow, each caught.
- The `build` job of `pypi.yml` itself: the build and the wheel smoke test run on every pull request that touches the package.

