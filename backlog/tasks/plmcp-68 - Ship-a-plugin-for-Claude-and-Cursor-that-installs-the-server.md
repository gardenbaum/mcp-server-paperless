---
id: PLMCP-68
title: Ship a plugin for Claude and Cursor that installs the server
status: To Do
assignee: []
created_date: '2026-11-23T11:37:49Z'
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
- [ ] #1 A plugin in the repository installs the released server in Claude Code and asks for the Paperless URL and token without writing the token into a committed file
- [ ] #2 The plugin passes claude plugin validate and the directory's documented pre-submission checks that can be run locally
- [ ] #3 The plugin loads in Cursor's documented plugin format
- [ ] #4 Tests keep the plugin manifests consistent with the package name and the documented settings
- [ ] #5 The README and docs tell users how to install it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
