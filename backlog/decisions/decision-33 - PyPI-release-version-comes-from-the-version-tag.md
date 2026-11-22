---
updated_date: '2026-11-22T23:21:21Z'
id: decision-33
title: PyPI release version comes from the version tag
date: '2026-11-22T23:20:08Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->

Decision-32 made a version tag publish to PyPI only when it equals the version in pyproject.toml, while image.yml (decision-28 as carried over by decision-31) takes its version from the tag alone. One tag thus had two version sources. The first tag after decision-32, v0.1.1 (2026-11-23), was pushed on a commit whose pyproject.toml still said 0.1.0: pypi.yml refused it and nothing reached PyPI, but image.yml published the GHCR image 0.1.1, whose package reports 0.1.0. A release that needs a version bump and a tag in the right order will be missed again. The owner (2026-11-23) chose to take the PyPI version from the tag as well.

<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->

**Supersedes decision-32.** Everything in decision-32 is carried over unchanged (the distribution name `mcp-server-paperless` and its console scripts, the uv_build `module-name` until PLMCP-54, the project URLs, Trusted Publishing from `pypi.yml` in the `pypi` environment without an API token, the separate workflow with its pull request trigger, the build once and publish those files rule, the smoke test, no cache, `id-token: write` only in the tag-gated publish job, the ancestor check, the PEP 740 attestations, pinned actions, no `continue-on-error`, no expression inside a script, the recommended repository settings, no publishing by hand, the README keeping the Git URL until the first release, its consequences and enforcement), except the rules below and what follows from them: decision-32's consequence that a release needs the version in pyproject.toml set before the tag, and its enforcement of the tag-equals-version check, are replaced as well. The rules that change:

- "On a tag the job first requires the tag to be `v` followed by the version exactly as `uv version --short` prints it" becomes: on a `v*` tag the build job first sets the package version from the tag, `uv version --frozen -- "${TAG#v}"`, with the tag passed through `env`; `--` keeps a tag such as `v--bump=patch` from being read as an option. uv refuses a tag that is not a valid PEP 440 version, which fails the build, so nothing is published; it writes the normal form (`v1.2.3-rc.1` gives `1.2.3rc1`). `--frozen` leaves uv.lock alone; `uv build` does not read it. The smoke test then proves the wheel reports that version.
- "one tag publishes the image and the PyPI release together when it names a plain version" becomes: a version tag publishes both, the image under the tag's semver and the PyPI release under its PEP 440 form, including pre-releases. PyPI refuses local versions (`+…`), so such a tag publishes only the image; release tags carry none.
- A release needs no version bump. The version in pyproject.toml on main is the development version: what builds from a clone, a Git URL or a pull request report.

<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->

- A release is one step: push a `v*` tag on a commit on main. A version number on PyPI can never be reused, so a tag that published is final; a tag whose PyPI build failed can be deleted and pushed again, but image.yml then publishes its image a second time.
- `uvx --from git+...@main`, a clone and the container image report the pyproject.toml version, not the latest tag: the image installs the locked project (`uv sync --locked`), so its package keeps reporting the development version while the image itself carries the tag. Bumping pyproject.toml now and then keeps that close; it is not required for a release.
- The tag v0.1.1 stays: its image is published, PyPI has nothing for it. The first PyPI release comes under a new tag after PLMCP-54.

<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_pypi.py`: every rule carried over from decision-32 keeps its tests (marker decision-32); the new step (marker decision-33) is found by name, runs only on `v*` tags, takes the tag from `env`, runs exactly `uv version --frozen -- "${TAG#v}"` before the build, and the old tag-equals-version step is gone.
- The `build` job of `pypi.yml`: its smoke test compares the wheel's version with `uv version --short`, which on a tag is the version just taken from it; `tests/quality/test_pypi.py` requires that comparison.
