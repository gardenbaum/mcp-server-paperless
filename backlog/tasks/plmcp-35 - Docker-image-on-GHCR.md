---
id: PLMCP-35
title: Docker image on GHCR
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-22T19:27:49Z'
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
- [x] #2 CI builds the image on main and publishes it to GHCR on version tags
- [x] #3 The image is scanned for known vulnerabilities in CI
- [x] #4 README documents running the container next to Paperless
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Decision-28 records the image rules. Health check is a TCP connect (python socket) to 127.0.0.1:PAPERLESS_MCP_HTTP_PORT: no route, no new code (decision-27 holds). Image: uv stage + python:3.14-slim builder + runtime, digests pinned with renovate comments, uid 10001, read-only fs, entrypoint paperless-mcp-http bound to 0.0.0.0, no baked-in tokens. Workflow image.yml is separate from ci.yml (never required): build + Trivy (HIGH/CRITICAL, ignore-unfixed, .trivyignore) + scripts/image_smoke.sh on PRs touching image inputs and on main; publish job only on v* tags with packages: write, provenance mode=max and SBOM. Local evidence: docker build OK (325MB on disk, 74MB compressed content); just-equivalent smoke test passes (refuses without tokens, read-only, cap-drop ALL, non-root, healthy, 401 without token, initialize 200); against the E2E stack on its docker network get_instance_overview returned Paperless 3.4.2 data; just check passes. UNVERIFIED: the publish job (nothing was pushed, no tag created; AC #2 stays unchecked until the first v* tag), the Trivy scan and the workflow in real GitHub Actions (Trivy could not be run in the sandbox), PR CI run. Local builds in this sandbox needed the proxy CA injected through a throwaway Dockerfile copy; the repo Dockerfile is unchanged by that. Deferred: scanning the pushed digest instead of the scanned build; tag vs pyproject version check.

Review round: removed pip and upgraded OS packages in the runtime stage (the scan failed on zlib1g, and msgpack/urllib3/setuptools vendored in the base pip); ARG/LABEL moved last; publish now pushes by digest, scans and smoke-tests that digest, then tags it; ancestor-of-main check before publishing; no syntax directive; README compose uses port 8001, joins an external network and notes PAPERLESS_ALLOWED_HOSTS; smoke test checks 421 for a foreign Host; guards parse image.yml with PyYAML (added as a dev dependency) and allowlist COPY sources. Evidence on the final commit: just check passes; image 326MB on disk; import pip fails; smoke test passes (read-only, non-root, healthy, 401, 421, initialize). A Trivy 0.58 run on the merged filesystem showed no Python findings; zlib1g could not be upgraded in the sandbox (Debian mirror unreachable), so the apt-get upgrade is verified only by CI. Recommended owner settings, not set: a tag ruleset for v* tags and a protected environment for publish.

Owner approved tagging v0.1.0 (2026-11-17) and is fine with a public GHCR package. This commit sets the package version to 0.1.0 so the tag matches pyproject. AC #2 is checked once the tag's publish job succeeds.

Controller, 2026-11-22: the owner created v0.1.0 on 1424b1d; image.yml run 45060119945 (push v0.1.0) built and published (both jobs success). Verified: an anonymous docker pull of ghcr.io/gardenbaum/paperless-mcp:0.1.0 works (the package is public), the image runs as 10001:10001 with label version 0.1.0 and refuses to start without PAPERLESS_URL/PAPERLESS_TOKEN.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Hardened multi-stage image (non-root, read-only, TCP health check, Trivy scan, smoke test) built on main and published to GHCR on v* tags; 0.1.0 is public at ghcr.io/gardenbaum/paperless-mcp.
<!-- SECTION:FINAL_SUMMARY:END -->
