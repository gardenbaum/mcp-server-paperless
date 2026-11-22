---
id: decision-29
title: >-
  Pin paperless-ngx 3.4.2 as the minimum supported release; cover the latest 3.x
  weekly
date: '2026-11-22T15:24:58Z'
status: accepted
---

## Context

Paperless-ngx 3.5.x changed behaviour that 3.4.2 (the pinned release) does not have: every document operation is queued and answers a `task_id`, reading the application configuration needs `view_applicationconfiguration`, blank notes are rejected, the mail password mask is fixed at 10 characters, and deleting a tag is refused when it would take child tags of other users with it (3.4 deletes them silently). PLMCP-46 made the adapter handle both releases and made the live tests branch on the release with `since()`. The open question was whether the pin should move to 3.5.x, and how the fake and the vendored docs follow.

## Decision

- The pin stays at 3.4.2, the oldest supported 3.x release: `PAPERLESS_VERSION` in the justfile, the image digest in `tests/e2e/compose.yaml` and the vendored docs in `docs/vendor/paperless`. Every pull request and the nightly `e2e-full` run therefore test the minimum.
- The latest stable 3.x is covered by the weekly `e2e-latest` workflow (and on demand by dispatching it).
- FakePaperless models both releases behind a release setting (`FakeState.release`), defaulting to the pin. The contract tests whose behaviour differs run against the fake in both modes and against the live stack.
- The vendored docs stay at the pin; the weekly drift check reports the differences to the latest release.
- The pin moves only by a new decision that supersedes this one (for example when 3.4 is dropped).

## Consequences

- A regression that only shows on 3.5 is caught weekly, not per pull request, unless the fake's 3.5 mode covers it.
- Live tests keep branching on the release with `since()`; the fake reports its release through the same `expected_version`.
- The vendored schema lacks the 3.5 additions: `task_id` on the document-operation results, the note `format` field and the manual workflow trigger type 5.
- The fake models the 3.5 behaviour that a port can observe. The mail password mask is not returned by any port, so only the live tests see it. Queued document operations are not modelled in the fake (the adapter's wire tests cover both answer shapes).

## Enforcement

- `tests/fakes/test_paperless.py` and the contract tests marked `decision-29` (`tests/contract`): the fake's release setting and the version-branching contract tests run on the fake in 3.4.2 and 3.5 mode and on the live stack.
- `tests/quality/test_e2e_ci.py` and `tests/quality/test_scheduled_jobs.py` keep the nightly `e2e-full` run on the pinned stack and the weekly `e2e-latest` run on the latest release.
