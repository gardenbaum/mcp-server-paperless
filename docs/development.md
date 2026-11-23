# Development

How the project is built, checked and run. The design is in
`backlog/docs/doc-1 - paperless-mcp-design.md`, the decisions in `backlog/decisions/`.

## Set up

Requires [uv](https://docs.astral.sh/uv/), [just](https://just.systems) and Go (pre-commit builds
the pinned gitleaks that `just check` runs over the whole history). The live tests need Docker.

```sh
just install                  # create the environment
uv run pre-commit install     # run the gates on every commit
just check                    # the full local gate, identical to CI
just --list                   # all recipes
```

## Architecture

```
entry points (cli, app)  →  server.py (composition root)
                                 ├── tools/      MCP tools, prompts, annotations
                                 ├── services/   use cases: name resolution, confirmation, previews
                                 ├── domain/     models, errors, ports (no I/O, no MCP)
                                 └── paperless/  the HTTP adapter for the Paperless REST API
```

`server.py` is the only module that knows both `tools/` and `paperless/`. Tools call services,
services depend on ports, and the Paperless adapter implements them; import-linter contracts fail
the build when a layer reaches past its neighbour. Because the code is written by AI agents, the
rules are enforced by checks, not by convention: every accepted decision names the test, contract
or lint rule that holds it.

## How it is tested

| Level | What it proves |
|---|---|
| Unit, adapter and tool tests | Services against fakes, the adapter against the vendored Paperless OpenAPI schema, every tool through an in-process MCP client |
| Contract suite | The same tests run against `FakePaperless` and against a real Paperless, so the fake cannot drift from the real thing; where 3.4 and 3.5 differ, the fake runs as both |
| Live end-to-end tests | The server against a real paperless-ngx in Docker: the touched toolsets on every pull request, the whole suite after every merge and nightly |
| Surface snapshot | Tool names, schemas, descriptions and annotations as clients see them; a change is reviewed |
| Mutation testing | mutmut on `services/` and `paperless/` with an 80 % gate, weekly |
| Latest release and drift | The live suite against the latest paperless-ngx 3.x, and the vendored schema against the latest release, weekly |

Changed lines need 100 % test coverage (`just cov-diff`); the whole code base at least 95 %.

```sh
just e2e-up                   # start the pinned Paperless stack for the live tests
just e2e                      # the live suite against it
just mutation                 # mutmut on services/ and paperless/, fails below the 80 % gate, a few minutes
just e2e-latest               # live suite against the latest stable paperless-ngx 3.x image
just drift-check              # vendored Paperless schema and docs vs. the latest 3.x release
```

The last three also run as weekly GitHub workflows and by manual dispatch.

## Documentation

`docs/tools.md` is generated from the tool surface: run `just docs-tools` after a tool, its
description or its annotations changed. Tests fail when it is stale, when the README states a
tool or toolset count the catalogue does not have, when a setting is documented nowhere under
`docs/`, or when a relative link leads nowhere.

## Conventions

Lint suppressions name a specific code and a reason, e.g. `# noqa: S101 -- reason`
(see `just hygiene`). Inline `# pragma` comments are banned: coverage exclusions are reviewed
`exclude_also` patterns in `pyproject.toml`.

Work is planned in `backlog/` with [Backlog.md](https://github.com/MrLesk/Backlog.md): one task,
one branch, one pull request, the failing test first. `just backlog-update` installs the latest
Backlog.md CLI and refreshes the managed block of CLAUDE.md and AGENTS.md non-interactively (the
CLI's own menu is driven through a pseudo-terminal; only that block may change); review and
commit the diff. A weekly workflow (`.github/workflows/backlog-drift.yml`, also runnable by hand)
compares the latest release on npm with the `backlog.md-instructions-version` header of both
files and keeps one `backlog-drift` issue open while they are behind (`just
backlog-drift-check` runs the comparison locally).

## Releases

A release is a version tag on a commit on main, pushed by the owner; nobody publishes by hand and
no version bump is needed (decision-33): `git tag -a v1.2.3 -m "1.2.3" && git push origin v1.2.3`.
The tag runs two workflows: `image.yml` publishes the container image to GHCR (decision-28 as
carried over by decision-36), and `pypi.yml` sets the package version from the tag (`uv version`,
PEP 440: `v1.2.3-rc.1` becomes `1.2.3rc1`) and publishes `mcp-server-paperless` to PyPI by Trusted
Publishing from the `pypi` environment. A tag that is not a valid version, or a commit that is not
on main, publishes nothing to PyPI; PyPI also refuses local versions (`+…`). The version in `pyproject.toml` is the development version that
a clone, a Git URL or the container's package report; raise it now and then.

The same tag also runs `registry.yml` (decision-35): once PyPI serves the version, it writes that
version into `server.json` and publishes the listing `io.github.gardenbaum/mcp-server-paperless`
to the [MCP Registry](https://registry.modelcontextprotocol.io) by GitHub OIDC; Glama claims it
through `glama.json`. Check a changed `server.json` with `mcp-publisher validate`.
