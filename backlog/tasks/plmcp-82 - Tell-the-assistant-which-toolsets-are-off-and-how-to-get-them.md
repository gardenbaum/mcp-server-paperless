---
id: PLMCP-82
title: Tell the assistant which toolsets are off and how to get them
status: In Progress
assignee: []
created_date: '2026-11-23T12:14:52Z'
updated_date: '2026-11-23T16:04:13Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 62000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An assistant saw only the default profile and reported trash, PDF tools, bulk changes, mail, workflows, views and tasks as missing, although the server has them. Neither the server instructions nor get_instance_overview says anything about toolsets. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 get_instance_overview and the server instructions name the toolsets that are off, what each does, and the setting and restart that turn them on; nothing is shown that read-only mode, disabled tools or a missing capability excludes
- [ ] #2 It is right for stdio, HTTP, the plugin and the container image
- [x] #3 Whether toolsets can be switched on per session is examined in a decision (security: the operator keeps the last word)
- [x] #4 The added tokens (instructions and overview) are measured and stay under a limit that this task sets and a test enforces (today's budget counts tools only)
- [x] #5 Contract tests on the fake and Paperless 3.4.2, tool tests and E2E cover every combination of selected toolsets, read-only mode, disabled tools and a missing capability
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
1. Pure function toolset_guide (tools/selection.py) over the catalogue tables, the selection, the hidden tools and the capability state: toolsets that are off but would add at least one tool; none that read-only mode, disabled tools or a known-missing capability exclude; an unchecked capability (ai unknown or Paperless silent at start) is shown with that gap. Tests first.
2. Server instructions: built in create_server (capability unchecked) and rebuilt after the startup probe; names the toolsets that are on and off, what each does (the exact remaining tools when some are excluded), the setting PAPERLESS_MCP_TOOLSETS with the full new value, and the restart per channel (stdio client, HTTP process, plugin option Toolsets in Claude Code, container environment). Existing 2048-byte instructions limit stays and is tested for the worst case.
3. get_instance_overview returns the same guide (ServerOverview with toolsets); surface snapshot regenerated deliberately.
4. Token limit: no tokenizer in the project, its method is UTF-8 bytes (TEXT_LIMIT); measure before and after, set a tight byte limit for the added text, enforce in a test.
5. Every combination: toolsets x read-only x disabled tools x capability (true, false, unknown, Paperless silent) on the fake; capability dimension on fake and live 3.4.2 (contract, admin and restricted user); e2e over stdio and HTTP on the live stack.
6. Decision (proposed) on per-session switching, evidence from docs/vendor/fastmcp (visibility.mdx per-session rules, sessions.mdx).
7. Docs: README, docs/configuration.md, docs/tools.md, docs/container.md, plugin README, per channel.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (branch plmcp-82-toolset-hints). A pure toolset_guide (tools/selection.py) works out, from the catalogue tables and what the start knows, the toolsets on and the ones the operator could switch on: a toolset is listed only if switching it on adds at least one tool after read-only mode and disabled tools; a toolset whose capability is known missing is never listed; one whose capability could not be checked (ai flag null, or Paperless silent at start) is listed with "needs Paperless AI, not checked"; a cut-down toolset is described by its remaining tools ("only list_trash"), not by what it is for. The server instructions are built in create_server (nothing probed yet: capability unchecked) and replaced after the startup probe on every start; get_instance_overview returns ServerOverview (the instance plus toolsets). The sentence "Start with get_instance_overview" is now left out where that tool is not callable.

Surface snapshot (tests/tools/__snapshots__/test_surface.ambr) regenerated deliberately for the three profiles: only get_instance_overview changes (one description line, class description, the toolsets output property).

Tokens: the project has no tokenizer, its limits count UTF-8 bytes (TEXT_LIMIT 2048). Measured in bytes (about 4 per token): instructions 331 -> 1190 on the default profile, at most 1392 over the stress combinations; overview call (fake) 715 -> 3325 default, guide at most 2742; overview definition in tools/list 4442 -> 5079 (+637); default tools/list 69218 -> 69855. Limits enforced in tests/tools/test_toolset_budget.py: instructions 1400, guide in a call 2750, definition growth 650, each within 10 percent of the measured worst case so they must be lowered when the text shrinks. Tool budget unchanged: 19 of 20.

AC 3: decision-41 (proposed): operator only, no per-session switching; evidence vendored visibility.mdx and sessions.mdx. Not accepted by me.

Evidence: fast tier tests/tools (matrix of 192 combinations: capability present/missing/unknown/silent x 6 selections x 4 disabled sets x read-only), contract on fake and live 3.4.2 (tests/contract/test_instance_reader.py), live in-process matrix (65), stdio and HTTP real processes that apply the setting the guide names and then offer exactly the promised tools, ai_e2e on a Paperless with AI on (3 passed). Hand mutations: 20, 17 killed; 2 survived as equivalent or out of the test selection (m11, m18), 1 killed by the budget test. Gates: just check pieces lock-check, lint, format-check, typecheck, imports, deps, secrets, diff-cover 100 percent pass; the full test run fails only test_the_menu_driver_reports_a_failing_cli (macOS, known), so DoD 2 stays unchecked. Left unchecked: AC 2 (plugin and image are proven by manifest, Dockerfile and docs tests, not by running Claude Code or the built image), DoD 1 (the contract, live and quality tests were written after the code and shown red against main afterwards), DoD 3 and 4 (PR).

Correction: 20 hand mutations, 18 killed, 2 survived: m11 (overview sentence ignoring the capability: equivalent today, no toolset that needs a capability holds the overview tool) and m18 (summaries[name] instead of summaries.get: only the synthetic catalogue of test_visibility needs the fallback, outside the mutation run).
<!-- SECTION:NOTES:END -->
