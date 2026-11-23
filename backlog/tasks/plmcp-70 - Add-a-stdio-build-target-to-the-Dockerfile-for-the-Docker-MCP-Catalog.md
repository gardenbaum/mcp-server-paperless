---
id: PLMCP-70
title: Add a stdio build target to the Dockerfile for the Docker MCP Catalog
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T11:39:56Z'
updated_date: '2026-11-23T11:41:29Z'
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
