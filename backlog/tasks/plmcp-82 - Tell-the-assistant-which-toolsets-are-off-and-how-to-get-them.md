---
id: PLMCP-82
title: Tell the assistant which toolsets are off and how to get them
status: In Progress
assignee: []
created_date: '2026-11-23T12:14:52Z'
updated_date: '2026-11-23T17:51:02Z'
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
- [x] #2 It is right for stdio, HTTP, the plugin and the container image
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
Implementation (PLMCP-82). A pure toolset_guide (tools/selection.py) works out which toolsets are on and which the operator could switch on, from the catalogue tables and what Paperless says about AI. A toolset is listed off only if switching it on adds at least one tool after read-only mode and disabled tools; one whose capability is known missing is never listed; one whose capability is unchecked (ai flag null, or Paperless silent) is marked "needs Paperless AI, not checked", on or off, by the current answer; a selected toolset that was hidden at the start and is available now is listed in the overview under offered_after_restart (the instructions are written at the start, when nothing has changed, and never mention it); a cut-down toolset is said to offer "some of its tools" and the instructions name no tool (the overview lists the exact tools). The instructions are composed in one place (server.describe_toolsets), first with nothing known, then after every startup probe; the text says who acts (the operator; ask the user, do not edit the configuration), gives the setting before the list with a placeholder (PAPERLESS_MCP_TOOLSETS=core,ai,<name>) and never offers `all`. get_instance_overview returns ServerOverview (the instance plus toolsets: on, offered_after_restart, off, switch_on, unchecked) built on each call from the AI flag it just fetched: what is off follows what a restart would deliver, what is on stays as the start found it. The sentence "Start with get_instance_overview" is left out where that tool is not callable.

Channels: Claude Code plugin option Toolsets, restart Claude Code; Cursor plugin has no option (only URL and token); stdio env of the server entry (docker run -i: -e; Docker MCP Catalog configuration), restart the client; HTTP and image environment, restart the process or recreate the container (docker restart keeps the old environment). Run for real: the image built and run over HTTP (default, env, restart keeps, recreate changes), a server started with exactly the manifest's environment (the option set, and set then cleared: refuses to start), real stdio and HTTP processes applying the setting the guide names for a toolset that is not the first (trash, raw, permissions). Claude Code and Cursor themselves were not run.

Bytes (the project has no tokenizer; its limits count UTF-8 bytes, about 4 per token): instructions 331 -> 1354 default profile, at most 1447 in the searched worst case (limit 1450; below TEXT_LIMIT 2048); guide in an overview call at most 2758 over both AI states (limit 2760); overview definition in tools/list 4442 -> +747 (limit 750); the limits were raised by exactly the bytes of the longer field name offered_after_restart. Limits in tests/tools/test_toolset_budget.py, within 10 percent of the worst case. Tool budget unchanged: 19 of 20. Surface snapshot regenerated deliberately: only get_instance_overview changes.

Tests: matrix on the fake (capability present, missing, unknown, silent x 6 selections x 4 disabled sets x read-only; refusals listed), oracle checks tools, what, switch_on value and gaps; real adapter against a closed port; contract on fake and live 3.4.2 (4 kinds of user); live matrix per kind of user on the answer Paperless gave, with Paperless AI off and on (the on case turns ai_enabled on through /api/config/ and restores it, no LLM backend needed, so it runs in the normal e2e run); real stdio and HTTP restarts. Hand mutations: 45 plus a reorder mutation, all killed except m11 (overview sentence ignoring the capability: equivalent while no capability-gated toolset holds the overview tool).

CI weights (scripts/e2e_ci.py MODULE_SECONDS and HOSTED_SECONDS in tests/quality/test_e2e_ci.py): test_toolset_guide 62 s, test_http 15 s, test_instance_reader 16 s are ONE pull-request-tier run, 45223357290, not a median of full runs; PLMCP-44 (open) has the refresh after the merge in its notes (its 548 s and 492 s are from run 45214768651 on the first version, not from a merge). decision-41 is accepted by the controller under the owner's delegation of 2026-11-23; its registry test catches calls, references, aliases, getattr and string uses of the session visibility methods, and server.enable/disable outside server.py.

Not verified: Claude Code and Cursor running the plugin; the hosted-runner time of the changed live modules beyond that one run; token counts with a real tokenizer. DoD 1 stays unchecked (the contract, live and quality tests were written after the code and shown red against main afterwards), DoD 2 (macOS-only failure test_the_menu_driver_reports_a_failing_cli), DoD 3 and 4 (PR).
<!-- SECTION:NOTES:END -->
