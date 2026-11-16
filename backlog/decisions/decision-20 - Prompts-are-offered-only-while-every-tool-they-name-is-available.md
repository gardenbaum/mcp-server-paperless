---
id: decision-20
title: Prompts are offered only while every tool they name is available
date: '2026-11-16T23:07:00Z'
status: accepted
---
## Context

Design 5.6 adds prompts (`triage_inbox`, `find_document`, `annual_review`, `cleanup_taxonomy`): guided tasks the model runs with the normal tools, always proposing before applying. A prompt is only text, but the text names tools. If a prompt names a tool that does not exist, belongs to a toolset the user did not select, is hidden by read-only mode or by a disabled-tools list, or needs a Paperless capability that is missing, the model is told to call something it cannot call. The registry rule that every component carries exactly one toolset tag (decision-4) leaves open what a prompt that needs several toolsets is tagged with, and how prompts follow toolset selection and read-only mode.

## Decision

- A prompt is tagged with exactly one toolset, as every component is. It declares in `SPECS` (tools/prompts.py) the toolsets it needs and every tool it names; its tag is one of the declared toolsets.
- A prompt names a tool only as a bare backticked snake_case identifier, for example `search_documents`. Parameters are written with a value (`inbox=true`) or in plain words, so the names can be read from the rendered text.
- A prompt is offered only while every tool it names can be called: its toolsets are selected, and read-only mode, `PAPERLESS_MCP_DISABLED_TOOLS` and a missing Paperless capability have not hidden any of those tools. The composition root decides this after the startup probe, on each start, from the catalogue (the selected toolsets, the hidden tools and the missing capabilities), not from a tool listing, so that a caller-dependent listing (auth, PLMCP-34 to 37) cannot hide prompts for good. A prompt without an entry in `SPECS` is hidden. A prompt is hidden whole, never offered in part.
- Every prompt ends with the shared confirmation rule: propose the changes, wait for the user to confirm, then call writing tools. Prompts never run writes by themselves and the server has no agent logic (design non-goals).
- Prompts are not tools: they do not count against the default profile's tool budget. The default profile (`core`, `ai`) offers the prompts whose tools it has.

## Consequences

- In read-only mode only prompts that name no writing tool remain (`find_document`).
- A prompt that names an optional tool, such as an `ai` tool, disappears when the toolset is off or Paperless has no AI; the shipped prompts name core tools only, so they are offered in every profile that has core.
- A prompt that names a tool by prose or as a call (`name(...)`) is caught by the registry test, which scans the text for every known tool name.
- Changing a prompt's text means keeping its `SPECS` entry equal to the tools it names; the test fails otherwise.
- The surface snapshot lists the prompts of each profile; any change to them is reviewed as a diff.

## Enforcement

- tests/tools/test_prompts.py renders every prompt and fails when it names a tool that does not exist in the real registry (`TOOL_TOOLSETS`) or is not in the prompt's declared toolsets, when `SPECS` differs from the tools and toolsets the text uses, or when the confirmation rule is missing. Self-tests run the check over a synthetic provider of violating prompts, including a real tool named in prose or as a call.
- The same file proves the visibility rule on the assembled server: unselected toolset, read-only mode, a disabled tool and a missing capability each hide the prompts that need them, per start.
- The registry test (tests/tools/test_registry.py) already requires exactly one toolset tag on prompts; the syrupy surface snapshot (tests/tools/test_surface.py) lists the prompts per profile.
