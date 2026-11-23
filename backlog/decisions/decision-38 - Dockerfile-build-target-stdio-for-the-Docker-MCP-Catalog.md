---
id: decision-38
title: Dockerfile build target stdio for the Docker MCP Catalog
date: '2026-11-23T11:43:48Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The Docker MCP Catalog builds a local server from the repository's Dockerfile and runs it over stdio. Its tool (docker/mcp-registry at 7acac06) runs `docker buildx build -f Dockerfile --target <source.buildTarget> <source.project>.git#<source.commit>` and starts the image with `docker run --rm -i --init --cap-drop=ALL -e ... <image> <run.command>`: an entry can name a build target and arguments after the image, but no entrypoint. The image of decision-28 (as carried over by decision-37) starts the HTTP server, and decision-28 says stdio is not a documented mode of the image and lists the Dockerfile's stages. The owner approved a stdio build target on 2026-11-23.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes decision-37.** Everything in decision-37, and what it carries over from decision-27, decision-28, decision-31, decision-32, decision-33 and decision-36, is carried over unchanged (including its consequences and enforcement), except:

- Decision-28's runtime stage becomes three: `base` (the pinned Python image with the security fixes, without pip, with the user 10001:10001 and the copied environment), and on top of it, metadata only, `stdio` and `runtime`. `runtime` is the last stage, so it stays the default target; it is the published image, with the same layers and configuration as before.
- `stdio` starts `mcp-server-paperless` as 10001:10001. It inherits nothing of the HTTP runtime: no `PAPERLESS_MCP_HTTP_*`, no exposed port and no health check (nothing listens, and a probe of a closed port would report a working server as unhealthy). It sets no variable of the server; Paperless URL and token come from the environment, and the server does not start without them.
- Decision-28's "stdio is not a documented mode of the image" still holds for the published image; the build target `stdio` is documented in docs/container.md.
- The `build` job also builds the target natively on each platform, after the scan and smoke test of the default image, and runs `scripts/image_stdio_smoke.sh` on it. The `publish` and `tag` jobs do not change: this repository never publishes the target. It is not scanned on its own, because it adds no layer to the filesystem the scan has seen.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The catalog builds the target from a commit of this repository with its own pipeline; its image, signatures, updates and scans are Docker's, not covered by the publish job.
- The environment and user lines stand in both stages, so that the published image keeps its exact configuration; a guard keeps them equal.
- No label is set on the target: the catalog adds the revision itself, and the labels of the runtime describe HTTP.
- Submitting the catalog entry and sharing test credentials is the owner's step.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_container.py` (marker decision-38): the stage order with `runtime` last on `base`, the exact instructions of `stdio`, only RUN and COPY in `base`, the same user and environment in both, the build and smoke test of the target in the `build` job without a condition, no target and no mention of stdio in `publish` and `tag`, the workflow path filter; the reviewed workflow snapshot.
- `scripts/image_stdio_smoke.sh` (`just image-stdio-smoke`, run by the `build` job on both platforms): refusal without a Paperless token, read-only with all capabilities dropped, uid 10001, MCP initialize and tools/list with nothing but JSON-RPC on stdout.
- Everything decision-37 lists under Enforcement still runs unchanged, except that the guards of the runtime stage read `base` and `runtime` together and the build job has a second build.
