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

### Demo recording

`just demo` records `docs/demo/demo.gif`, the recording at the top of the README. It needs only
Docker and network access: `docs/demo/compose.yaml` starts the pinned Paperless of the live tests
as a throwaway stack, and the recorder container (vhs and uv, pinned in `docs/demo/Dockerfile`)
fills it with the invented documents of `docs/demo/demo.py` and records `docs/demo/demo.tape`. The
stack and its volumes are removed afterwards. The script runs the released server from PyPI
(`SERVER` in `demo.py`) as an MCP client. Questions and tool calls are scripted and no model takes
part; the answers come from the server and are only shortened. `demo.gif` is replaced only when the
script reached its end. Keep the caption and the alt text in
the README true, look at every scene before committing, and stay within 30 seconds and 500 kB (a
test checks both; the large-file hook stops more). To show another release, change `SERVER` and
raise `UV_EXCLUDE_NEWER` in the Dockerfile, run `just demo` and update the caption; a test compares
the caption with `SERVER`. The caption names the Paperless of the recording, so it stays true when
the live tests move to a newer one.

Known limits: PyPI's page of an older release embeds the GIF of main, so it shows the newest
recording, not the one of its time. The recorder image stays behind after a run; remove it with
`docker rmi mcp-server-paperless-demo-recorder`.

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
no version bump is needed (decision-33, carried over by decision-40): `git tag -a v1.2.3 -m "1.2.3" && git push origin v1.2.3`.
The tag runs two workflows: `image.yml` publishes the container image to GHCR (decision-28 as
carried over by decision-40), and `pypi.yml` sets the package version from the tag (`uv version`,
PEP 440: `v1.2.3-rc.1` becomes `1.2.3rc1`) and publishes `mcp-server-paperless` to PyPI by Trusted
Publishing from the `pypi` environment. A tag that is not a valid version, or a commit that is not
on main, publishes nothing to PyPI; PyPI also refuses local versions (`+…`). The version in `pyproject.toml` is the development version that
a clone, a Git URL or the container's package report; raise it now and then.

The same tag also runs `registry.yml` (decision-35): once PyPI serves the version, it writes that
version into `server.json` and publishes the listing `io.github.gardenbaum/mcp-server-paperless`
to the [MCP Registry](https://registry.modelcontextprotocol.io) by GitHub OIDC; Glama claims it
through `glama.json`. Check a changed `server.json` with `mcp-publisher validate`.

The plugin in `plugin/` pins the release it runs, because the Claude directory refuses an unpinned
`uvx`. After PyPI serves a release, tick it in Renovate's Dependency Dashboard issue under "Pending
Approval": as observed in November 2026 (issue #9) the bot opens no pull request before that.
Renovate then raises `version` and the `mcp-server-paperless==` pin in both
`plugin/.claude-plugin/plugin.json` and `plugin/.cursor-plugin/plugin.json` in one pull request (a
test keeps the four in step and applies the regexes of `renovate.json` to them; it does not run
Renovate). Only plain versions (`1.2.3`) are proposed. Review and merge the pull request yourself:
the bot automerges by its own configuration, and `renovate.json` switches that off for this package.
Until then plugin users stay on the release before. The pin covers the package, not its dependencies. Check with
`claude plugin validate --profile directory plugin` and `claude plugin validate .`. In the
directory's review expect a hold "Runs a pinned npx or uvx package", which every pinned launcher
gets, and possibly a brand-name hold ("Name matches a known brand") for Paperless in the name.

The plugin's skills in `plugin/skills/` repeat the server's prompts (a test compares the text), so
change both together. They reach installed users only with a new plugin version, which is the pinned
package version: a changed skill is out with the next release's manifest pull request, not before.
Whoever installs the plugin anew gets main as it is, with the pinned release. When a prompt gains a
tool the pin lacks, the test still wants the skill changed with it: merge that change as the last
pull request before the release, tag, and merge the manifest pull request right after. In between a
new install has a skill that names a tool its server lacks, which the skill tells the assistant to
report; the steps without that tool still work.
