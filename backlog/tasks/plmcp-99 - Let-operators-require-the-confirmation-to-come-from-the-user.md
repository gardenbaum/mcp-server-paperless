---
id: PLMCP-99
title: Let operators require the confirmation to come from the user
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T18:44:54Z'
updated_date: '2026-11-24T07:37:29Z'
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
