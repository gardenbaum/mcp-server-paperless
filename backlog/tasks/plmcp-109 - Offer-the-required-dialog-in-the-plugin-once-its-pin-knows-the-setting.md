---
id: PLMCP-109
title: Offer the required dialog in the plugin once its pin knows the setting
status: To Do
assignee: []
created_date: '2026-11-24T08:07:14Z'
labels:
  - safety
  - config
dependencies: []
priority: medium
type: chore
ordinal: 84000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
PLMCP-99 leaves PAPERLESS_MCP_REQUIRE_DIALOG out of the plugin, because its pinned release ignores the setting, which would fail open. Due with the first plugin pin that knows it (expected 0.4.0).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 plugin.json offers the setting, off by default, and the README table and the option count in plugin/README.md match it
- [ ] #2 The sentence in docs/configuration.md that the plugin does not ask for it is gone
- [ ] #3 A test fails when the pinned release knows a setting that the plugin leaves out without a stated reason
- [ ] #4 An existing plugin installation without a value for the option still starts (checked with Claude Code)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
