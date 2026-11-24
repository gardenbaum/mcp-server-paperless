---
id: PLMCP-84
title: Keep document content and arguments out of the logs
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:18:23Z'
updated_date: '2026-11-24T21:57:40Z'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 72000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The work on the privacy policy (PLMCP-71) found four places where content can reach the log although the rule is that it never does at INFO: resource_boundary in tools/_shared.py logs Paperless's error text at INFO and FastMCP adds a traceback; a failed prompt logs its argument value in a FastMCP traceback; over HTTP a malformed Paperless startup answer is quoted in a uvicorn ERROR traceback; DEBUG over HTTP logs whole answers. Owner approved on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 At INFO and above none of the first three reaches stderr, proven by canary tests over stdio and HTTP, while an error stays diagnosable by type
- [x] #2 DEBUG over HTTP does not log answers, or the docs and the policy say plainly that it does and why
- [x] #3 PRIVACY.md and docs/configuration.md match the result
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Canary tests first (tests/test_private_logs.py): the real stdio and HTTP entrypoints in a subprocess against a stub Paperless, both protocol eras, INFO; DEBUG over HTTP; unit tests for the record factory and hooks.
2. One root-cause fix in observability.configure_logging: a log record factory that strips exception texts and tracebacks of every logger above DEBUG and names the types instead (full traceback as a DEBUG record), hooks for warnings, uncaught, thread and unraisable exceptions, no logging-internal error dumps, sse_starlette quieted.
3. resource_boundary logs by type; FastMCP's own resource record goes to DEBUG.
4. Startup failures (uvicorn lifespan message, stdio exit line) name fixed messages but never quote Paperless.
5. PRIVACY.md, docs/configuration.md, README.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first: 12 of 15 canary tests failed on main's code; green after. Design: one log record factory (observability.private_record) names exceptions by type above DEBUG for every logger; hooks cover warnings, uncaught, thread and unraisable errors; sse_starlette is held at WARNING, so no answer is logged at any level.
Deliberate test changes: test_exceptions_are_rendered_into_the_record (asserted that the text of an exception is logged at ERROR) and test_a_resource_domain_error_is_logged_at_info_with_its_hint (asserted the leak) now assert the opposite.
Further leaks fixed: FastMCP's resource address, startup 400/409 text on both transports, tracebacks of all libraries, asyncio's reprs, Python warnings, uncaught/thread/unraisable errors, arguments of a record that cannot be rendered. Reported, not changed (documented in PRIVACY.md): path of a failed Paperless request (model-chosen for paperless_api_get), the name of an unknown tool in the call record, sign-in values, HTTP path.

Review round (PR #153): grouped startup errors keep their fixed text; exceptions as message or argument become types; the factory cannot raise and no record is lost (raiseExceptions untouched, the assertion that pinned the silent loss changed); asyncio originals at DEBUG; place in the code for logged errors; failed resources named by CallLogging; unknown tool names cut to 100 characters. Limit, documented: text FastMCP itself formats into sign-in messages.
<!-- SECTION:NOTES:END -->
