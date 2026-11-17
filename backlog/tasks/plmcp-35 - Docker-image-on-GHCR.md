---
id: PLMCP-35
title: Docker image on GHCR
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-17T02:13:00Z'
labels: []
milestone: m-5
dependencies:
  - PLMCP-34
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 35000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Self-hosting next to Paperless needs a small, hardened image (design §8).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Multi-stage image runs as a non-root user with a health check
- [ ] #2 CI builds the image on main and publishes it to GHCR on version tags
- [x] #3 The image is scanned for known vulnerabilities in CI
- [x] #4 README documents running the container next to Paperless
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Decision-28 records the image rules. Health check is a TCP connect (python socket) to 127.0.0.1:PAPERLESS_MCP_HTTP_PORT: no route, no new code (decision-27 holds). Image: uv stage + python:3.14-slim builder + runtime, digests pinned with renovate comments, uid 10001, read-only fs, entrypoint paperless-mcp-http bound to 0.0.0.0, no baked-in tokens. Workflow image.yml is separate from ci.yml (never required): build + Trivy (HIGH/CRITICAL, ignore-unfixed, .trivyignore) + scripts/image_smoke.sh on PRs touching image inputs and on main; publish job only on v* tags with packages: write, provenance mode=max and SBOM. Local evidence: docker build OK (325MB on disk, 74MB compressed content); just-equivalent smoke test passes (refuses without tokens, read-only, cap-drop ALL, non-root, healthy, 401 without token, initialize 200); against the E2E stack on its docker network get_instance_overview returned Paperless 3.4.2 data; just check passes. UNVERIFIED: the publish job (nothing was pushed, no tag created; AC #2 stays unchecked until the first v* tag), the Trivy scan and the workflow in real GitHub Actions (Trivy could not be run in the sandbox), PR CI run. Local builds in this sandbox needed the proxy CA injected through a throwaway Dockerfile copy; the repo Dockerfile is unchanged by that. Deferred: scanning the pushed digest instead of the scanned build; tag vs pyproject version check.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Adds a hardened multi-stage Dockerfile (non-root uid 10001, digest-pinned bases, read-only friendly, TCP-probe health check), .dockerignore, .trivyignore, scripts/image_smoke.sh with just image / image-smoke, workflow image.yml (build, Trivy scan, smoke test; tag-gated publish with provenance and SBOM), README container section with compose example (tokens and allowed hosts, no unauthenticated flag), decision-28 and tests/quality/test_container.py guards. Verified locally by build, smoke test and a run against the E2E stack; just check passes. Not verified: publishing (AC #2), Trivy and the workflow in GitHub Actions, required CI on the PR, merge (DoD #1 and #4 open).
<!-- SECTION:FINAL_SUMMARY:END -->
