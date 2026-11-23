---
id: PLMCP-70
title: Add a stdio build target to the Dockerfile for the Docker MCP Catalog
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T11:39:56Z'
updated_date: '2026-11-23T11:49:36Z'
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
- [ ] #1 A build target of the Dockerfile runs mcp-server-paperless over stdio as the same unprivileged user
- [ ] #2 The published HTTP image is unchanged and stays the default target
- [ ] #3 The stdio target is built and answers initialize and tools/list in CI, and its workflow and container guards cover it
- [ ] #4 The catalog's own validate and build checks pass for an entry that uses the target
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
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
Deliberate: decision-38 supersedes decision-37; the workflow snapshot gains the two stdio steps and one path; the guards of the runtime stage read base and runtime together; the build job has two builds. The target is not scanned on its own: it adds no filesystem layer.
Catalog check (local clone, nothing pushed): validate passes for the GitHub project; build --tools passes on this branch's commit served by a local git daemon (17 tools, no tools.json needed, no Paperless reachable); catalog generation passes. The final entry needs the merged commit in source.commit.

just check: everything passes except the known macOS-only test_the_menu_driver_reports_a_failing_cli. Not verified locally: the linux/amd64 leg and the workflow run itself.
<!-- SECTION:NOTES:END -->
