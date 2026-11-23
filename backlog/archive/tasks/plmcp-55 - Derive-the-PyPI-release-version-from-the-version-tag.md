---
id: PLMCP-55
title: Derive the PyPI release version from the version tag
status: Done
assignee:
  - '@claude'
created_date: '2026-11-22T23:19:54Z'
updated_date: '2026-11-22T23:40:10Z'
labels: []
milestone: m-5
dependencies: []
priority: high
type: chore
ordinal: 55000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Owner, 2026-11-23: a release should need nothing but a version tag. The tag v0.1.1 was pushed right after PLMCP-53 merged; pypi.yml refused it because pyproject.toml still said 0.1.0 (nothing reached PyPI), while image.yml, which takes its version from the tag, published the GHCR image 0.1.1. Two version sources on one tag invite exactly this. The owner chose to take the PyPI version from the tag too, as image.yml does, which also lets semver pre-release tags such as v1.2.3-rc.1 publish to both. This changes a rule of accepted decision-32, so a new decision supersedes it. The tag v0.1.1 stays (its image is published); the first PyPI release comes after PLMCP-54 under a new tag.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 On a `v*` tag the build job sets the package version from the tag before building (`uv version --frozen`), so the built files carry the tag version in PEP 440 form; a tag that is not a valid version fails the build and publishes nothing
- [x] #2 The wheel smoke test proves the built wheel reports the version the tag names, and pull request builds keep the version in pyproject.toml
- [x] #3 A new decision supersedes decision-32, carrying everything over except the tag-equals-version rule; guard tests enforce the new step and its order
- [x] #4 docs/development.md describes a release as pushing a version tag on main, without a version bump
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Decision-33 (CLI create, body in the file) supersedes decision-32: everything carried over except the tag-equals-version rule, which becomes "on a v* tag the build sets the version from the tag with uv version --frozen"; pre-release tags then publish to both workflows; pyproject.toml on main is the development version. Mark decision-32 superseded by decision-33 with the CLI.
2. Red first in tests/quality/test_pypi.py (marker decision-33 on the changed test): the set-version step found by name, exact tag condition, TAG from the environment, exact command, before the build; the old check step gone.
3. pypi.yml: replace the check step with the set step; the smoke test keeps comparing the wheel with uv version --short, which after the step is the tag version.
4. docs/development.md Releases section: push a tag on main, no bump; pre-releases work.
5. PLMCP-54 AC #4: the release PR no longer sets a version.
6. Simulate the tag build locally (v0.2.0, v1.2.3-rc.1, an invalid tag), just check, PR.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause of the failed run 45082300097: tag v0.1.1 on 05f4f42 whose pyproject.toml said 0.1.0; the step "Require the tag to name the version being built" ran test "0.1.1" = "0.1.0" and exited 1. Nothing reached PyPI; image.yml published the GHCR image 0.1.1 (package reports 0.1.0).
Red first: the new guard test failed (no step named like "version from the tag"), then passed after replacing the check step.
Tag build simulated on a copy: v0.2.0 gives mcp_server_paperless-0.2.0 reporting 0.2.0; v1.2.3-rc.1 gives 1.2.3rc1 reporting 1.2.3rc1; v1.2.x is refused by uv version (exit 2); uv.lock unchanged each time.
The guard test was checked against five weakened workflows (if: false, an expression in the command, the step after the build, the step removed, the old check back): each caught.
Gate: lock, lint, format, basedpyright 0/0/0, 9 contracts kept, deptry, gitleaks green; 4635 tests passed (the two known macOS-only failures deselected), coverage 99.93 %; no src lines changed.

Review fix: the step runs uv version --frozen -- "${TAG#v}"; without -- a tag like v--bump=patch or v--short was read as a uv option and exited 0 with a version the tag never named (the smoke test compared two equal wrong values). Checked: v--bump=patch now exits 2, v1.2.3-rc.1 still gives 1.2.3rc1. The smoke test's version comparison is now guarded (a mutation removing it fails a test). Decision-33 names local versions (+...) as refused by PyPI and lists decision-32's replaced consequence and enforcement.

Merged as ca38b6d (#72): required checks, CodeQL and all e2e shards green; Codex review of the head commit e6533ea completed with no findings. The task was finalized in a follow-up PR because it was not set Done on its branch before the merge (doc-2 step 4 missed).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
pypi.yml now takes the PyPI version from the v* tag: on a tag the build job runs uv version --frozen -- "${TAG#v}" (tag via env) before building, replacing decision-32's tag-equals-version check that refused v0.1.1 while image.yml published it. uv refuses invalid versions and writes PEP 440 (v1.2.3-rc.1 -> 1.2.3rc1); the -- stops tags like v--bump=patch being read as options. Decision-33 supersedes decision-32; docs/development.md describes a release as one tag push on main; PLMCP-54's release criterion no longer bumps the version. Verified: guard tests red then green and mutation-checked, tag builds simulated on a copy, just check green (two known macOS-only tests deselected), CI green, Codex clean. Not verified: image.yml tagging for non-semver PEP 440 tags such as v1.0; PyPI's live refusal of local versions. The tag v0.1.1 stays (image only); the first PyPI release needs a new tag after PLMCP-54.
<!-- SECTION:FINAL_SUMMARY:END -->
