---
id: PLMCP-68
title: Ship a plugin for Claude and Cursor that installs the server
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T11:37:49Z'
updated_date: '2026-11-23T11:59:11Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The Claude directory takes local MCP servers only inside a plugin, and the Cursor marketplace takes plugins too. A plugin in this repository makes the install one step and opens both directories. Owner approved on 2026-11-23. Submission to the directories is the owner's step.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A plugin in the repository installs the released server in Claude Code and asks for the Paperless URL and token without writing the token into a committed file
- [x] #2 The plugin passes claude plugin validate and the directory's documented pre-submission checks that can be run locally
- [ ] #3 The plugin loads in Cursor's documented plugin format
- [x] #4 Tests keep the plugin manifests consistent with the package name and the documented settings
- [x] #5 The README and docs tell users how to install it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: tests/quality/test_plugin.py (manifests vs. package name, settings, pin, license, README, marketplaces); plugin/README.md joins the doc link and name guards.
2. plugin/ with .claude-plugin/plugin.json (userConfig, token sensitive), .cursor-plugin/plugin.json (variables), README, LICENSE; uvx pinned to the released version (the directory blocks unpinned launchers).
3. Root marketplace files (.claude-plugin, .cursor-plugin): needed because the plugin is a subfolder and users install by name from GitHub.
4. README, docs/configuration.md, docs/development.md (release step).
5. Verify: claude plugin validate --strict --profile directory; install, configure and start in an isolated CLAUDE_CONFIG_DIR; just check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented plugin/ for Claude Code and Cursor plus root marketplace files. Verified in a Linux container with Claude Code 2.1.330 and an empty CLAUDE_CONFIG_DIR: install, /plugin options, the released server connects through uvx, defaults reach the server, the token is only in .credentials.json. Red: 18 failed, green: 82 passed (test_plugin.py, test_docs.py). Cursor is not installed here: checked against its docs only. Pinned to 0.3.2 because the directory blocks unpinned launchers; a release needs a follow-up PR (docs/development.md). Open for the owner: listing icon (only taken on the first save or submission), Cursor options beyond URL and token, Cowork (ignores servers whose options have no default), automatic pin updates through Renovate.

Review fixes: the update sequence needs a marketplace refresh first; clearing Toolsets stores an empty value that 0.3.2 refuses (documented); the Cursor local copy is a load test only. AC3 (Cursor) is checked against Cursor's docs only, never loaded in Cursor. The icon (I4) waits for the owner.

Owner's icon added: plugin/assets/icon.png (1024 px, Claude icon and Cursor logo), assets/icon.svg (title translated to English) and assets/icon-400.png for other listings; not in the sdist or wheel. claude plugin validate --strict --profile directory passes without findings. Update sequence and the cleared-Toolsets behaviour reproduced in a container with a git marketplace.

Known limits from the review: a manifest option added in a feature PR appears before the pinned release understands it (M2); a manifest-only fix reaches installed users only with a new plugin version, which equals the package version (M3); the README install commands work only once the plugin is on main and are re-run once after the merge (M8). assets/icon.svg differs from the owner's file in one word of its title (English). Owner decisions 2026-11-23: icon supplied, Renovate rule is PLMCP-73, skills are PLMCP-72, Cursor stays URL and token only until tested by the owner.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
plugin/ installs the released server in Claude Code from the repository's marketplace and asks for seven options (URL and token required, token sensitive). It follows Cursor's documented plugin format; AC 3 stays unchecked because Cursor itself was not available and the owner tests it. Tests tie the manifests to the package name, settings, docs and icon. DoD 2: one known macOS-only local failure, CI green.
<!-- SECTION:FINAL_SUMMARY:END -->
