---
id: PLMCP-33
title: 'Prompts: triage_inbox, find_document, annual_review, cleanup_taxonomy'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-16T23:12:00Z'
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
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added four MCP prompts (triage_inbox, find_document, annual_review, cleanup_taxonomy) in tools/prompts.py. Each is tagged with one toolset, declares in SPECS the toolsets it needs and every tool it names, and ends with a shared confirmation rule (propose, wait for the user, then write; destructive tools via the confirm=true flow). Decision-20 records the new rule: a prompt is offered only while every tool it names can be called (toolset selected, not hidden by read-only mode, disabled tools or a missing capability), computed per start in the server lifespan; in read-only mode only find_document remains. AC2: tests/tools/test_prompts.py renders every prompt and checks the names it mentions (bare backticked identifiers) against the real TOOL_TOOLSETS registry and the declared toolsets, with self-tests over a synthetic violating provider. Verification: just check passed (ruff, format, basedpyright, import-linter incl. the independence entry for tools.prompts, 2848 tests, coverage 99.97 %, diff coverage 100 %). Surface snapshot changed deliberately: only prompt entries added in the default, all and read-only profiles; no tool changed, default tool budget unchanged. No services/ or paperless/ changes (no mutation run); no e2e added. Open: the independent opus review did not run (the reviewer session never started its turn), so no reviewer findings exist; DoD #1 stays unchecked because the new tests ran red as a whole (import error) and the visibility tests were shown to fail by removing the transform after the code existed, not each one individually before it; DoD #2 and #3 depend on the PR; DoD #4 is left unchecked.
<!-- SECTION:FINAL_SUMMARY:END -->
