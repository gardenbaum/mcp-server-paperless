---
id: PLMCP-90.2
title: 'Read-only toolsets b: plugin text with the pin that knows it'
status: To Do
assignee: []
created_date: '2026-11-25T12:22:11Z'
labels: []
dependencies:
  - PLMCP-90.1
parent_task_id: PLMCP-90
priority: medium
type: feature
ordinal: 108000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-90: the Claude Code plugin pins a release, and a pinned server without PLMCP-90.1 refuses to start on name:read. So the option's text may name the suffix only together with a pin of a release that contains PLMCP-90.1 (same rule as decision-53); until such a release exists nothing is prepared. Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen', all as recommended); file design-plmcp-90.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The plugin pins a release that knows name:read, and the description of the option Toolsets and the plugin README name it
- [ ] #2 A test ties the text to the pin; the Cursor plugin stays unchanged (owner decision 4)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
