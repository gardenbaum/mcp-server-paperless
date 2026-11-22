---
superseded_by: decision-31
updated_date: '2026-11-22T20:33:40Z'
id: decision-28
title: 'Container image: hardened, probed by TCP, published only on tags'
date: '2026-11-17T02:10:00Z'
status: superseded
---
## Context
<!-- SECTION:CONTEXT:BEGIN -->

Self-hosting next to Paperless needs a small image that is safe by default (design §8). Decision-27 makes the HTTP transport refuse to start without tokens and allows no route besides the MCP endpoint, so the image has to fit those rules instead of loosening them, and a container health check cannot use an HTTP route. Publishing to GHCR is irreversible outside the repository.

<!-- SECTION:CONTEXT:END -->
## Decision
<!-- SECTION:DECISION:BEGIN -->

- Dockerfile: a uv stage (`ghcr.io/astral-sh/uv`, used only to install), a builder (`python:3.14-slim`) that runs `uv sync --locked --no-dev --no-editable` into `/app/.venv`, and a runtime stage (the same `python:3.14-slim`) that copies only that environment. No compiler, uv or pip cache, source tree or test in the runtime image. Every base image is pinned by tag and digest with a `# renovate: datasource=docker` comment, so Renovate moves tag and digest together.
- The runtime runs as the numeric user 10001:10001 (no home, no login shell); the environment belongs to root and the server writes nothing, so the image works with `--read-only`, `--cap-drop ALL` and `no-new-privileges`. The build copies named paths only; `.dockerignore` keeps `.git`, `.env*`, tests, docs and Backlog files out of the context.
- The entrypoint is `paperless-mcp-http`, bound to `0.0.0.0` inside the container. The image sets no tokens, no allowed hosts and never the unauthenticated loopback flag: decision-27's refusal applies unchanged, so the container does not start until `PAPERLESS_MCP_HTTP_AUTH_TOKENS` and `PAPERLESS_MCP_HTTP_ALLOWED_HOSTS` are set. stdio is not a documented mode of the image.
- Health check: a TCP connect to `127.0.0.1` on `PAPERLESS_MCP_HTTP_PORT`, run by the image's own Python (`socket.create_connection`). It adds no route, no endpoint and no code, keeps decision-27's single route, and sends no request that could reach the logs or the auth layer. It says only that the server accepts connections, not that Paperless is reachable (an unreachable Paperless does not stop the server by design, and the tools report it). A health endpoint would need its own decision. A server bound to a specific non-loopback address through `PAPERLESS_MCP_HTTP_HOST` must override the check.
- Workflow `.github/workflows/image.yml`, separate from ci.yml so it can never be a required check or slow a merge: pull requests (only when the image inputs change) and main build the image without pushing, scan it and run `scripts/image_smoke.sh`. The scan is Trivy, failing on fixed HIGH and CRITICAL vulnerabilities (`ignore-unfixed`); reviewed exceptions live in `.trivyignore`, each with a reason and an expiry. Only a pushed `v*` tag runs the `publish` job, the only job with `packages: write`. It first requires the tagged commit to be an ancestor of origin/main, pushes the image by digest alone (provenance `mode=max` and an SBOM attached, no cache write), scans and smoke-tests exactly that digest, and only then gives it its semver tags with `docker buildx imagetools create`: what is tagged is what was scanned. A failed scan leaves an untagged digest. No `pull_request_target`, no `workflow_run`, every action pinned to a commit SHA with a version comment, `persist-credentials: false`. A tag ruleset limiting who may create `v*` tags and a protected environment are recommended repository settings, not set by the repository.
- Nobody publishes by hand. Pushing a version tag is the owner's decision.

<!-- SECTION:DECISION:END -->
## Consequences
<!-- SECTION:CONSEQUENCES:BEGIN -->

- The image is usable only with tokens and host names set; the README shows a compose file that does so without the unauthenticated mode.
- A scan failure after a base image digest moves blocks the image workflow, not merges; Renovate's digest bump is the fix, and an exception needs a dated `.trivyignore` entry.
- `ignore-unfixed` hides vulnerabilities without a fix: they surface once Debian ships one and the next scan runs (scans run on main and on image-related pull requests, not on a schedule).
- The runtime stage upgrades OS packages (`apt-get upgrade`) and removes the base image's pip, whose vendored msgpack and urllib3 the scan reports; nothing can install packages into the running image. The build is therefore not byte-reproducible from the digest pin alone; the scan of the pushed digest is the gate. `ARG VERSION` and `ARG REVISION` come last in the runtime stage so they never change the cache key of the scanned layers. The Dockerfile has no `# syntax` line (a floating frontend); `COPY` sources are limited to an allowlist.
- The health check proves the process serves, not that Paperless works.

<!-- SECTION:CONSEQUENCES:END -->
## Enforcement

- `tests/quality/test_container.py` (marker decision-28): digest pins with Renovate comments, numeric non-root user, entrypoint, TCP probe without HTTP, no baked-in credentials or unauthenticated flag, `.dockerignore`, SHA-pinned actions, no `pull_request_target`, `packages: write` only in the tag-gated publish job, the scan severity and ignore file on both scans, the scan and smoke test of the pushed digest before tagging, the ancestor check, separation from ci.yml, pip removal, ARG order and the COPY allowlist. The workflow is parsed with PyYAML.
- `scripts/image_smoke.sh` (`just image-smoke`, run by the `build` job of the image workflow): refusal without tokens, read-only and non-root run, healthy, 401 without a token and an MCP initialize with one.
- Whether the publish job works is not verified until the first version tag is pushed (manual, the owner's call).
