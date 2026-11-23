---
id: PLMCP-70
title: Add a stdio build target to the Dockerfile for the Docker MCP Catalog
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T11:39:56Z'
updated_date: '2026-11-23T15:33:39Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The Docker MCP Catalog builds a local server from its Dockerfile and runs the image over stdio. Our image starts the HTTP server, and the catalog's server.yaml can name a build target (source.buildTarget) but not an entrypoint. Owner approved on 2026-11-23. The catalog's form with test credentials is the owner's step.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A build target of the Dockerfile runs mcp-server-paperless over stdio as the same unprivileged user
- [x] #2 The published HTTP image is unchanged and stays the default target
- [x] #3 The stdio target is built and answers initialize and tools/list in CI, and its workflow and container guards cover it
- [x] #4 The catalog's own validate and build checks pass for an entry that uses the target
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Verify how the catalog builds and runs (buildx build --target <buildTarget> <repo>.git#<commit>; docker run -i --init --cap-drop=ALL image + run.command as CMD arguments).
2. Red: guards in tests/quality/test_container.py for the stdio stage, the default target and the workflow; scripts/image_stdio_smoke.sh fails while the target is missing.
3. Green: split the shared filesystem layers into a base stage; stdio (metadata only: env, user, entrypoint; no HTTP env, port or probe) before runtime, which stays last and unchanged; build job builds and smoke-tests the target, nothing publishes it.
4. decision-38 supersedes decision-37 (stdio target, stage list); docs/container.md, justfile, references.
5. Prove the HTTP image config and layers identical before and after; run just check, image-smoke, the catalog's validate and build against a local entry.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Catalog (docker/mcp-registry 7acac06): cmd/build runs docker buildx build -f Dockerfile --target <source.buildTarget> --load <project>.git#<commit>, so BuildKit handles the bind mounts and digest pins; the image runs as docker run --rm -i --init --cap-drop=ALL -e ... image <run.command>, so arguments but no entrypoint can be set.
Design: base (shared filesystem), stdio (ENV, USER, ENTRYPOINT only; no HTTP env, EXPOSE or HEALTHCHECK), runtime last. The default image built before and after has the same image id, config, layers and history.
Red: 6 guards failed, --target stdio was not found, the stdio smoke failed on the HTTP image. Green: 23 container guards, both smoke tests. Four Dockerfile mutations (default stage renamed, user, entrypoint, parent stage) each fail a guard.
Deliberate: decision-38 supersedes decision-37; the workflow snapshot gains the two stdio steps and one path; the guard of pip and OS upgrades reads base, the one of the argument order base and runtime; the build job has two builds. The target is not scanned on its own: the build job requires both images to have the same layers.
Catalog check (local clone, nothing pushed): validate passes for the GitHub project; build --tools passes on this branch's commit served by a local git daemon (19 tools, the README's default; no tools.json needed, no Paperless reachable). The first run found 17 because the registry's build passes the examples of config.env as values and the example of PAPERLESS_MCP_DISABLED_TOOLS named two ai tools; the example is now empty_trash, outside the default toolsets; catalog generation passes. The final entry needs the squash commit on main in source.commit; its icon is assets/icon-400.png on main.

just check: everything passes except the known macOS-only test_the_menu_driver_reports_a_failing_cli. Not verified locally: the linux/amd64 leg and the workflow run itself.

Review fixes: runtime holds metadata instructions only; FROM lines are read in any case and with flags, every FROM must be readable and every stage name unique; the build job compares RootFS.Layers of both images (deliberate snapshot change: one more step). Red first: of eleven Dockerfile mutations in a scratch copy eight passed the old guards, all eleven fail the new ones. The old smoke test accepted noise on stdout before and after the answers, a hang after stdin ends, exit status 3 and gid 0; the new one rejects each.
Decided: the catalog secret keeps required: true. It is metadata for the configuration UI; a token that is missing anyway reaches the server as the literal <UNKNOWN> (docker/mcp-gateway), which a reachable Paperless rejects, so the server stops at startup with its message.

Independent opus review and re-review: 0 Critical, 0 Important left. Both architectures built and passed the stdio smoke test and the layer comparison in the PR's CI. DoD 2 unchecked: one known macOS-only local failure, CI green. The catalog entry is submitted with the squash commit.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A stdio build target runs mcp-server-paperless as uid 10001 with the HTTP image's layers; the HTTP image is unchanged and the default. CI smoke-tests it on both architectures and proves the layers are equal. decision-38 supersedes decision-37. Docker's validate and build pass for an entry that uses the target (19 tools).
<!-- SECTION:FINAL_SUMMARY:END -->
