---
id: decision-57
title: The server warns about a Paperless older than the minimum release
date: '2026-11-24T08:14:37Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-43 makes 3.5.1 the minimum but lets the server accept any release with API version 10, so 3.4 is accepted and fails later with unexpected answers. The owner approved a warning on 2026-11-23 (PLMCP-101).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- This supersedes one part of decision-43: "the server does not check the version (a startup check is a separate proposal)". Everything else of it stands.
- The minimum is `MINIMUM_RELEASE` in `domain/instance.py`, compared with the `X-Version` header of an answer that also carries `X-Api-Version` (Paperless sends both together, to an authenticated user), so that a gateway or a login page with an `X-Version` of its own is not believed. Only `X.Y.Z` in ASCII digits is compared; anything else, and a missing header (read as `unknown`), gives no warning.
- It is a warning: an older release does not stop the start and takes no tool away.
- The operator gets one warning line per start, from the first answer that reports the old release, so also when Paperless was unreachable at start.
- `get_instance_overview` says it in a field of its own, `too_old`: the found release, the minimum and that Paperless must be updated.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- `paperless_version` stays the plain release; `too_old` is null where there is nothing to say. It costs 132 bytes of output schema: `DEFINITION_ADDED_LIMIT` rises from 750 to 879, approved by the owner on 2026-11-24.
- A Paperless behind a proxy that removes `X-Version` (but keeps `X-Api-Version`) no longer fails the overview and the start.
- A proxy that overrides `X-Version` on genuine answers cannot be told apart: its release is compared.
- The hint for a refused API version (406, which carries no release) names the minimum too.
- A pre-release of the minimum is reported by Paperless as the plain release or is not compared.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- The sentence in `paperless_version`: no more schema, but the field would no longer be a version that a caller can compare.
- A comparison at the start only: says nothing when Paperless was unreachable then.
- Refuse to start: not what was approved.
<!-- SECTION:ALTERNATIVES:END -->


## Enforcement

- `tests/domain/test_instance.py`: older, equal, newer and unreadable versions. `tests/quality/test_reference_release.py`: the code states the minimum once and it is the pinned release the docs name.
- `tests/adapter/test_client.py`, `tests/adapter/test_instance.py` and `tests/test_server.py`: one log line, also after an unreachable start, and a missing header.
- `tests/tools/test_core.py`, `tests/contract/test_instance_reader.py` and `tests/e2e/test_minimum_release.py`: the overview, on the fake and on a real Paperless.
