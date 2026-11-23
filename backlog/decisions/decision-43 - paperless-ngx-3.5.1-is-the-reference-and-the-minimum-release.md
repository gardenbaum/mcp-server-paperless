---
updated_date: '2026-11-23T19:54:16Z'
id: decision-43
title: paperless-ngx 3.5.1 is the reference and the minimum release
date: '2026-11-23T19:32:43Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-29 pinned 3.4.2 as the minimum supported release and covered the latest 3.x weekly, so that every change had to work on 3.4.2 and 3.5.x, the fake had a release setting with a 3.4 mode, and live tests branched on the release. Paperless-ngx 3.5.0 queues every document operation behind a task, rejects blank notes, asks for view_applicationconfiguration on a config read, refuses to delete tags that would take other users' child tags, masks mail passwords with a constant, and adds the manual workflow trigger. The owner decided on 2026-11-23 that the whole repository moves to 3.5.1 and that 3.5.1 is the minimum: simpler code, one behaviour to test. decision-2 states the reference as the 3.4.2 image with E2E on 3.4.2 on every pull request (its meaning, 3.x and API version 10 only, stands). decision-21 lists the four trigger types and the action types the server cannot write.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- The reference release is paperless-ngx 3.5.1: `PAPERLESS_VERSION` in the justfile, the image digest in `tests/e2e/compose.yaml`, the vendored docs in `docs/vendor/paperless` and the E2E helpers name the same release.
- 3.5.1 is the minimum supported release. Paperless-ngx 3.4.x, 3.5.0 and 2.x are not supported; the server does not check the version (a startup check is a separate proposal).
- Pull requests run the fast, not required E2E tier on 3.5.1 (decision-23, decision-24); the full suite runs on 3.5.1 after every merge to main and nightly (`e2e-full`). The weekly `e2e-latest` run and the weekly drift check cover the latest stable 3.x.
- The fake models 3.5.1 only; live tests do not branch on the release. A version comparison is added only when a later release changes behaviour that a test must tell apart.
- The pin and the minimum move only by a new decision that supersedes this one.
- Replaces decision-29 (minimum, fake default, vendored docs) and the statements that name 3.4.2 as the vendored and E2E release in decision-2 and in the test-strategy level of decision-8. The 3.4.2 mentions in decisions 14, 16, 18, 21, 23, 24 and 26 report what was probed or measured then and stay as history; where they describe behaviour that 3.5.1 changed, 3.5.1 governs.
- Extends decision-21 without changing its meaning: a manual workflow trigger (type 5, new in 3.5, run by hand from the web UI) is read and listed but not written, and an update that replaces the triggers of a workflow refuses to delete one this server cannot write, as it does for actions. The confirmation text of an enabled workflow with a manual trigger says that it runs when a user runs it by hand, not by itself. Writing manual triggers is PLMCP-87.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Users of 3.4.x or 3.5.0 have to upgrade. Against them some tools fail or answer differently (the document operations answer without a task id, which the adapter refuses as an unexpected answer; blank notes and the configuration read behave as before 3.5). There is no version check, so the error does not name the version.
- The code that handled the 3.4 answers is gone: optional task ids, the fake release setting and its 3.4 mode, the `since` and release-branching tests, and the 3.4 sentences of the tool texts.
- Behaviour that 3.5.1 itself needs stays: the adapter follows the queued task, the PDF writer still looks for the file tasks of an operation, and wire deviations of the schema that were probed on 3.4.2 and not re-probed stay listed in the schema guard.
- A regression on a release newer than 3.5.1 is caught weekly, not per pull request.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Keep 3.4.2 as the minimum and test it weekly or nightly (decision-29): more code and a second behaviour to keep in the fake; rejected by the owner.
- Move the reference to 3.5.1 and keep 3.4.2 supported without testing it: no proof that it works; rejected.
<!-- SECTION:ALTERNATIVES:END -->


## Enforcement

- `tests/quality/test_reference_release.py`: the justfile, the E2E compose image (tag and digest), the E2E helpers and the vendored `UPSTREAM` name one release, and the README, the configuration docs and the plugin README name it as the minimum.
- `tests/fakes/test_paperless.py` and the contract tests marked `decision-43` (`tests/contract`, `tests/adapter`): the fake models the 3.5.1 behaviour (blank notes, configuration permission, tag deletion) and the same tests run on the live stack.
- `tests/quality/test_e2e_ci.py` and `tests/quality/test_scheduled_jobs.py` keep the nightly `e2e-full` run on the pinned stack and the weekly `e2e-latest` run on the latest release; `just drift-check` reports the vendored docs against the latest release.
- `tests/services/test_workflows.py`: manual triggers are read, not written, not dropped by a replacement and described as run by hand.
