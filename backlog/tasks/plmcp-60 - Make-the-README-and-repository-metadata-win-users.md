---
id: PLMCP-60
title: Make the README and repository metadata win users
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T00:52:45Z'
updated_date: '2026-11-23T02:14:42Z'
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
- [x] #1 The README opens with a one-sentence value proposition, a 60-second setup for Claude Code, Claude Desktop, Cursor and VS Code (one-click install links where the client offers them), and a short comparison with the other paperless-ngx MCP servers
- [x] #2 Badges show the PyPI version, monthly downloads, CI, supported paperless-ngx and Python versions and the licence
- [x] #3 pyproject keywords, classifiers and project URLs are complete for PyPI search
- [x] #4 The GitHub description, homepage and topics are set
- [x] #5 Every claim in the README (tool counts, test counts, versions) matches the code, checked by the docs test where one exists
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Comparison facts: counted from the sources of OrellBuehler (126 tools), freeformz (120), baruchiro (47), nloui (16), November 2026; clones in the session scratchpad. The first GitHub description claimed "most complete"; replaced because OrellBuehler and freeformz have more tools.
GitHub description, homepage and 19 topics set with gh repo edit.
Not verified: the VS Code and Cursor install links were built after the community format and not clicked in the editors. A social preview image needs the GitHub web UI (owner). Listing in the MCP Registry and awesome lists is outward-facing and left for the owner to approve.
Evidence: test_docs.py + test_pypi.py 53 passed; lint, format, lock check clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The README now opens with a value proposition, the Claude Code command, PyPI/downloads/CI/version/licence/stars badges and one-click installs for VS Code and Cursor, compares the server with the other paperless-ngx MCP servers from their sources, and has VS Code and Cursor quick starts and an mcp-name marker for the MCP Registry. pyproject gained a description, keywords, classifiers and a Changelog URL (guarded by a test); the GitHub description, homepage and topics are set. Follow-ups for the owner: social preview image, MCP Registry and awesome-list submissions.
<!-- SECTION:FINAL_SUMMARY:END -->
