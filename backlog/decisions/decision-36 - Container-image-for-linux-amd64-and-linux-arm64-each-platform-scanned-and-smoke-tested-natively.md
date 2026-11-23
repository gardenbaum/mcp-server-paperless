---
superseded_by: decision-37
updated_date: '2026-11-23T11:11:39Z'
id: decision-36
title: >-
  Container image for linux/amd64 and linux/arm64, each platform scanned and
  smoke-tested natively
date: '2026-11-23T09:34:57Z'
status: superseded
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The image of decision-28 (as carried over by decision-31) was built on one amd64 runner, so 0.3.0 does not start natively on a Raspberry Pi, an ARM NAS or Apple Silicon, where many paperless-ngx users self-host. The owner asked for ARM on 2026-11-23. Publishing one digest and tagging it in the same job cannot hold for two platforms.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes decision-31.** Everything in decision-31, and what it carries over from decision-27 and decision-28, is carried over unchanged (including its consequences and enforcement), except the image workflow:

- The `build` and `publish` jobs run once per platform, linux/amd64 and linux/arm64, each on a GitHub runner of that architecture (no emulation), with a build cache scope per platform. Every leg builds, scans with Trivy and runs `scripts/image_smoke.sh`; every publish leg still checks that the tagged commit is on main and pushes by digest alone with provenance and SBOM.
- A new `tag` job, which needs every publish leg and runs only for a pushed `v*` tag, gives the two scanned digests their version tags as one image index. Only `publish` and `tag` hold `packages: write`; `tag` checks out no code. The digests travel as workflow artifacts and must look like a sha256 digest.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- One tag resolves for both platforms. If one platform fails, nothing is tagged and untagged digests remain.
- The tag job runs at the first version tag only; before that it is verified by guards, not by a run. Pull requests and main build, scan and smoke-test both platforms.
- The image of 0.3.0 stays amd64 only.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/quality/test_container.py` (markers decision-28 and decision-36): the runner matrix, one native platform per leg, cache scopes, no emulation, scan and smoke test of the pushed digest before it is recorded, the exact tag script and steps, the job gates and permissions, the Trivy platform, and the whole workflow without action pins as a snapshot, so any added condition or changed step shows up as a diff.
- Everything decision-31 lists under Enforcement still runs unchanged.
