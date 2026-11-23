---
id: PLMCP-60
title: Make the README and repository metadata win users
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T00:52:45Z'
updated_date: '2026-11-23T01:23:26Z'
labels: []
dependencies:
  - PLMCP-58
type: chore
ordinal: 57000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The owner wants the project presented so that as many paperless-ngx users as possible find it, try it and star it. The repository has one star, no topics, no homepage, a terse description, and the README opens with process detail rather than a fast path to a working setup in each client.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The README opens with a one-sentence value proposition, a 60-second setup for Claude Code, Claude Desktop, Cursor and VS Code (one-click install links where the client offers them), and a short comparison with the other paperless-ngx MCP servers
- [ ] #2 Badges show the PyPI version, monthly downloads, CI, supported paperless-ngx and Python versions and the licence
- [ ] #3 pyproject keywords, classifiers and project URLs are complete for PyPI search
- [ ] #4 The GitHub description, homepage and topics are set
- [ ] #5 Every claim in the README (tool counts, test counts, versions) matches the code, checked by the docs test where one exists
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
1. Research the other paperless-ngx MCP servers from their sources (tool counts, toolsets, confirmation, saved views, auth, tests) for a factual comparison.
2. README hero: value proposition, PyPI/downloads/CI/versions/licence/stars badges, VS Code and Cursor install links, the Claude Code command up front, mcp-name marker for the MCP Registry.
3. Comparison table; VS Code and Cursor quick-start sections.
4. pyproject description, keywords, classifiers, Changelog URL; a PyPI test guards keywords and the Python classifier.
5. GitHub description, homepage and topics via gh repo edit.
6. Merge after PLMCP-58: the table claims the full-text notice.
<!-- SECTION:PLAN:END -->
