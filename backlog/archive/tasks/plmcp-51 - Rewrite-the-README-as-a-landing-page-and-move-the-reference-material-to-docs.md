---
id: PLMCP-51
title: Rewrite the README as a landing page and move the reference material to docs/
status: Done
assignee:
  - '@claude'
created_date: '2026-11-22T21:16:39Z'
updated_date: '2026-11-22T21:36:23Z'
labels:
  - docs
dependencies: []
priority: medium
type: docs
ordinal: 51000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Owner, 2026-11-22: the README must become much more attractive so the project gets found and starred. Today it opens with a stale status line ('one tool so far'; the catalogue has 87 tools in 13 toolsets), carries the whole reference (HTTP, OIDC, container, a single table cell with every toolset) as a wall of text, and misses PAPERLESS_MCP_READ_ONLY and the bulk and admin toolsets. Make the README a landing page (what it is, what you can ask, why this server, quick start, toolset overview) and move the reference into docs/ pages it links to. Every number and claim must be true and stay true: tests tie the README and docs to the catalogue and the settings.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The README is a landing page: tagline and badges, example requests, highlights with verified numbers, quick starts for Claude Code, Claude Desktop and the container that were run as written, a toolset overview and links to the docs
- [x] #2 The reference (HTTP transport and OIDC, container, configuration with every variable and toolset, development) lives in docs/ pages; nothing documented before is lost and the gaps (read-only mode, bulk and admin toolsets) are filled
- [x] #3 docs/tools.md lists every tool with its kind and one-line description, generated from the tool surface; a test fails when it or the README's tool and toolset counts drift from the catalogue, when a settings variable is undocumented, or when a relative link is broken
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Tests first: tests/quality/test_docs.py (README counts vs TOOL_TOOLSETS/TOOLSETS, every toolset named in the README overview, every settings variable in docs/, relative links resolve) and tests/tools/test_tools_doc.py (docs/tools.md equals the page rendered from the wire surface; --write-docs / just docs-tools regenerates it). Run red.
2. Move the reference into docs/http.md, docs/claude-ai-connector.md, docs/container.md, docs/configuration.md, docs/development.md; restructure the toolset cell into per-toolset sections and add read-only mode, bulk and admin.
3. Write the landing-page README; run each quick start as written (uvx from git, docker run of the published image).
4. just check; PR; suggestions for the owner on repository settings (description, topics, social preview) and a 0.2.0 release, which are outside the repository files.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Tests first: tests/quality/test_docs.py and tests/tools/test_tools_doc.py were written before the pages and failed (9 failed: no counts in the README, no toolset sections, undocumented settings, docs/tools.md missing); green after the pages were written.
Verified by running them as written: the Claude Code and Claude Desktop command (uvx --from git+https://github.com/gardenbaum/paperless-mcp paperless-mcp, also with @v0.1.0) through a FastMCP stdio client against the pinned paperless-ngx 3.4.2 stack (16 tools with AI off, 4 prompts, get_instance_overview answered); the docker run of ghcr.io/gardenbaum/paperless-mcp:0.1.0 (401 without the token, initialize 200 with it).
Numbers in the README come from the catalogue and are pinned by tests (87 tools, 13 toolsets, 18 by default, the per-toolset counts); the test counts are stated as lower bounds (637 live tests and 4594 others collected today).
Gaps filled in docs/configuration.md: PAPERLESS_MCP_READ_ONLY, the core, bulk and admin toolsets (from the tool descriptions and decision-26). Wording softened: the claude.com callback is described as Anthropic's other domain, not as an announced move (not verified).
Found on the way: the update_user description contradicts itself about renames (PLMCP-52). Not done here: the published image is 0.1.0 and lacks the OIDC layer and the 3.5 support; docs/container.md says so. A 0.2.0 release, the repository description, topics and social preview are the owner's (outside the repository files).
DoD #2 and #4 are checked for the merge of this PR by the controller.

Follow-up after the merge (owner, 2026-11-22): the 'Why this server' table was hard to understand without context. It is now 'Why another paperless-ngx MCP server?': one sentence saying that several such servers exist (without naming or linking them there; the Credits section still does), then the six points as a list in plain language. The two 'all of the Paperless API' phrasings were toned down to 'most of'.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The README is now a landing page (tagline, badges, a diagram, example requests, highlights, verified quick starts for Claude Code, Claude Desktop, the container and claude.ai, a toolset overview, the safety model, compatibility) and the reference moved to docs/: configuration (every variable, every toolset including the previously missing read-only mode, bulk and admin), HTTP, the claude.ai connector, the container and development. docs/tools.md lists all 87 tools and 4 prompts and is generated from the tool surface (just docs-tools). New tests tie the documentation to the code: tool, toolset and default-profile counts, a section per toolset, every settings variable documented, relative links and anchors valid, the tool reference current. No tool, gate or decision changed.
<!-- SECTION:FINAL_SUMMARY:END -->
