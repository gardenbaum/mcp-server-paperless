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
- The minimum is `MINIMUM_RELEASE` in `domain/instance.py`, compared with the `X-Version` header. Only `X.Y.Z` in ASCII digits is compared; anything else, and a missing header (read as `unknown`), gives no warning.
- It is a warning: an older release does not stop the start and takes no tool away.
- The operator gets one warning line per start, from the first answer that reports the old release, so also when Paperless was unreachable at start.
- `get_instance_overview` says it in `paperless_version`: the found release, the minimum and that Paperless must be updated.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- For an old release `paperless_version` is a sentence that begins with the release; for every other it is the release alone, as before.
- A Paperless behind a proxy that removes `X-Version` (but keeps `X-Api-Version`) no longer fails the overview and the start.
- A pre-release of the minimum is reported by Paperless as the plain release or is not compared.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- A field of its own in the overview: more bytes of output schema in every session than `DEFINITION_ADDED_LIMIT` has left (3 of 750); the limit would have to rise.
- A comparison at the start only: says nothing when Paperless was unreachable then.
- Refuse to start: not what was approved.
<!-- SECTION:ALTERNATIVES:END -->


## Enforcement

- `tests/domain/test_instance.py`: older, equal, newer and unreadable versions. `tests/quality/test_reference_release.py`: the code states the minimum once and it is the pinned release the docs name.
- `tests/adapter/test_client.py`, `tests/adapter/test_instance.py` and `tests/test_server.py`: one log line, also after an unreachable start, and a missing header.
- `tests/tools/test_core.py`, `tests/contract/test_instance_reader.py` and `tests/e2e/test_minimum_release.py`: the overview, on the fake and on a real Paperless.
