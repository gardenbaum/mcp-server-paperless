---
id: PLMCP-109
title: Offer the required dialog in the plugin once its pin knows the setting
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T08:07:14Z'
updated_date: '2026-11-24T21:07:40Z'
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
- [x] #1 plugin.json offers the setting, off by default, and the README table and the option count in plugin/README.md match it
- [x] #2 The sentence in docs/configuration.md that the plugin does not ask for it is gone
- [x] #3 A test fails when the pinned release knows a setting that the plugin leaves out without a stated reason
- [x] #4 An existing plugin installation without a value for the option still starts (checked with Claude Code)
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
1. Red: test_plugin.py: every setting the stdio server reads is offered by the plugin or left out with a stated reason (pure check with a self-test); the option count in plugin/README.md matches.
2. How the test learns the settings of the pinned release, offline: from the settings classes of main, a superset. A setting newer than the pin is listed with the pin that lacks it; that reason expires when the pin is raised, so the manifest pull request turns red until it is offered or left out for good.
3. Green: both manifests to 0.4.0, option in plugin.json, both README tables, plugin/README.md count, docs/configuration.md.
4. AC 4: claude plugin validate (both), the substitution code of Claude Code, and if possible a run with an isolated configuration directory.
5. just check, draft PR.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: 2 failed (REQUIRE_DIALOG not offered; option count). Green: test_plugin, test_docs, test_registry.

AC 3: the test takes the settings from the classes on main, a superset of the pinned release (the settings.py in the 0.4.0 wheel on PyPI is byte-identical to main today). No network, no Git tag (CI checkouts may be shallow), no recorded copy of a release to forget. A setting newer than the pin is listed in TOO_NEW with that pin; raising the pin ends the exemption. Limits: whether a setting is really unknown to the pin is stated by a person, and the test does not notice an option offered before the pin knows it (as before). Left out with a reason: MAX_UPLOAD_BYTES, MAX_DOWNLOAD_BYTES, LOG_LEVEL, STARTUP_TIMEOUT.

AC 4: Claude Code 2.1.330, isolated CLAUDE_CONFIG_DIR, --plugin-dir, the released 0.4.0 from PyPI, unreachable Paperless: with saved options but none for the new key (only the two required; and all eight old ones) the server is Connected and gets PAPERLESS_MCP_REQUIRE_DIALOG=false; true is passed as true. The code agrees: each option without a saved value gets its manifest default. A saved empty string is passed on empty and the server refuses to start: fails closed, like Read-only. Not exercised: marketplace update and the interactive dialog.

claude plugin validate (both) passed. Cursor: only the pin; it offers the two required variables only and leaves the setting unset (off).

decision-53 still names the old pin 0.3.2 in its consequences; it asks for exactly this change, so it is unchanged.
<!-- SECTION:NOTES:END -->
