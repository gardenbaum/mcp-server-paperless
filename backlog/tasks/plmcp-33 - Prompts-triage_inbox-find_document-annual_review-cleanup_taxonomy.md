---
id: PLMCP-33
title: 'Prompts: triage_inbox, find_document, annual_review, cleanup_taxonomy'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-16T23:22:00Z'
labels: []
milestone: m-4
dependencies:
  - PLMCP-16
  - PLMCP-19
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 33000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Guided multi-step tasks belong in prompts the LLM runs with the normal tools, not in server-side agent logic; prompts always propose changes before applying them (design §5.6, non-goals).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The four prompts exist, each declares the toolsets it needs and tells the model to propose and wait for confirmation before writing
- [x] #2 A test fails when a prompt names a tool that does not exist or is not in its declared toolsets
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
1. Decision-20 (created with the CLI, body written in the file): a prompt carries one toolset tag, declares the toolsets it needs and the tools it names in SPECS; it is offered only while every tool it names can be called (toolsets, read-only, disabled tools, capabilities); tool references are bare backticked snake_case identifiers so tests can extract them.
2. Red first: tests/tools/test_prompts.py (prompts exist and declare, propose-first rule, AC2 check over TOOL_TOOLSETS with synthetic-violator self-tests, visibility rules on the assembled server). Then green: src/paperless_mcp/tools/prompts.py, Catalogue.prompt_specs, a prompt Visibility rule recomputed per start in the lifespan.
3. import-linter: tools.prompts in the toolset independence contract. Surface snapshot updated deliberately (prompt entries only); default tool budget unchanged.
4. just check, reviewer, squash.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented tools/prompts.py (four prompts, SPECS table, shared CONFIRMATION), Catalogue.prompt_specs, prompt Visibility rule recomputed on each start from the tools actually listed, import-linter independence entry, decision-20. Tests (tests/tools/test_prompts.py) written first: first run red at import; visibility tests proven red by removing the transform. Surface snapshot updated deliberately: only prompt entries were added (216 lines, none removed) for default, all and read-only profiles; tool lists and the default tool budget are unchanged. No services/ or paperless/ changes, so no mutation run; no e2e added.

Review round 1 (independent opus reviewer, via coordinator): no Critical. I1 fixed: the test scans rendered text for every TOOL_TOOLSETS name as a whole word and for backticked call forms; self-tests with synthetic prose and call-form prompts (the helper lives in the test file, so those self-tests passed on first run; the new checks on the real prompts and the new behaviour tests were run red). M1 fixed: prompt visibility is computed from the catalogue (selected tools minus unavailable toolsets), not from server.list_tools(); decision-20 updated. M2 fixed: a prompt without a SPECS entry is hidden. M3: cleanup_taxonomy kind is Literal[tag, correspondent, document_type, storage_path] | None; surface snapshot updated deliberately for the two profiles that list it (argument description and schema only). M4, M5, M6: prompt text corrected (rename vs merge, delete_taxonomy_item refuses first only, triage skips already reviewed documents). M7: confirmation_problems helper shared by both tests. DoD #1 stays unchecked.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added four MCP prompts (triage_inbox, find_document, annual_review, cleanup_taxonomy) in tools/prompts.py. Each is tagged with one toolset, declares in SPECS the toolsets and tools it needs, and ends with a shared confirmation rule (propose, wait for the user, then write; destructive calls through the confirm=true flow). Decision-20: a prompt is offered only while every tool it names can be called, computed per start from the catalogue (selected toolsets, hidden tools, missing capabilities); a prompt without a SPECS entry is hidden; in read-only mode only find_document remains. AC2: tests/tools/test_prompts.py renders every prompt, extracts the backticked tool names, checks them against the real TOOL_TOOLSETS and the declared toolsets, and fails on any tool named in prose or as a call; self-tests use synthetic violating prompts. Review: the independent reviewer found no Critical issue; I1 and M1 to M7 were fixed in the fix commit (see notes). Verification: just check passes (lint, types, import-linter, tests, 100 % diff coverage). Surface snapshot changed deliberately: prompt entries added; in the fix commit only the cleanup_taxonomy kind argument schema changed. No tool entry changed, default tool budget unchanged. No services/ or paperless/ changes (no mutation run); no e2e added. DoD #1 unchecked: the tests were not each seen red before the code (first run red as a whole at import). DoD #2 and #3 depend on the PR; DoD #4 unchecked.
<!-- SECTION:FINAL_SUMMARY:END -->
