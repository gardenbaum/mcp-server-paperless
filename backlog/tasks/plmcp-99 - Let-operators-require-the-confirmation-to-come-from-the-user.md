---
id: PLMCP-99
title: Let operators require the confirmation to come from the user
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T18:44:54Z'
updated_date: '2026-11-24T07:53:28Z'
labels:
  - safety
  - config
dependencies:
  - PLMCP-98
documentation:
  - docs/vendor/fastmcp/docs/servers/elicitation.mdx
priority: medium
type: feature
ordinal: 75000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
With confirmation dialogs the assistant can still skip the dialog by passing confirm=true on its first call, for example after a prompt injection in a document. That is right as a default, because clients without elicitation have no other way and a user may already have asked for exactly this. An operator whose client can show dialogs has no way to make the human answer binding.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A setting, off by default, makes every confirmation require an accepted dialog; confirm=true alone then changes nothing
- [ ] #2 With the setting on, a client without form elicitation gets an error that names the reason and the setting, and nothing is changed
- [ ] #3 The setting covers every tool with a confirm parameter, including bulk and admin, with a registry-wide test
- [ ] #4 SECURITY.md and the configuration reference state what it protects against and that a client can answer dialogs automatically, so it is no proof of a human
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Setting PAPERLESS_MCP_REQUIRE_DIALOG (ServerSettings.require_dialog, off); create_server puts it into Dialogs.required.
2. tools/confirmation.py: with it on, the boundary drops confirm=true before the tool runs, so every confirmation goes through ConfirmationRequired.require and only an accepted dialog passes; every way back to the plain refusal (no forms, no such method, target kept changing, no client) and every NotConfirmed becomes a NotConfirmed that names reason and setting.
3. Tests first: registry-wide over both servers (HTTP and stdio variants) and both eras, wire tests, stateless HTTP; e2e on both entrypoints.
4. decision-53, SECURITY.md, configuration reference, README, server.json, plugin.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
PAPERLESS_MCP_REQUIRE_DIALOG (decision-53). The boundary drops confirm=true, so every confirmation reaches ConfirmationRequired.require and only an accepted dialog passes; every other outcome is a NotConfirmed with reason and setting. Tests red first (unknown setting). tests/quality/test_registry.py: the new boolean joins the table of formats of server.json. No snapshot, limit, gate or tool text changed. The plugin offers the option while it pins 0.3.2, which ignores it: the option and its docs say so. mode='auto'/'legacy' and HTTP/stdio variants are covered registry-wide; stateless HTTP on the wire. E2E written, run on GitHub only.

Review fixes: the AST check reads every module (keyed by path) and allows only the caller's own confirm; exact reasons on the wire; NO_FORMS true in every case; the refusal names the way out where there was nobody to ask; tool is positional-only in confirmed(); HTTP variants of the upload tools tested (the HTTP download tools have no confirm). CORRECTION of the note above: the plugin does NOT offer the option; it pins 0.3.2, which would silently ignore it. It is added with the first pin that knows the setting, together with the README table (a test keeps them equal). server.json has it: its version is written from the tag, a test forces its list.
<!-- SECTION:NOTES:END -->
