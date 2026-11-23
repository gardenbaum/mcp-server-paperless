---
updated_date: '2026-11-23T16:04:45Z'
id: decision-41
title: >-
  Toolsets are switched on by the operator, never by the assistant or per
  session
date: '2026-11-23T15:39:37Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
An assistant that saw only the default profile reported trash, PDF tools, bulk changes, mail, workflows, views and tasks as missing although the server has them (PLMCP-82). The server now tells the assistant which toolsets are off, what each adds and what the operator sets and restarts (server instructions and `get_instance_overview`). The question this decision settles: should the assistant, or a session, be able to switch a toolset on itself?

Evidence from the vendored FastMCP 4 docs:
- `servers/visibility.mdx`, "Per-Session Visibility": a tool can call `ctx.enable_components()` so that only that session sees more tools; rules accumulate per session and a `tools/list_changed` notification goes to that session.
- The same page, "Sessions on MCP 2026-07-28": that protocol era has no session. Rules are scoped to the authenticated user and apply to every connection that user makes (so a switch outlives the conversation); stdio keeps one set of rules for the process; on unauthenticated HTTP `enable_components()` raises instead of changing anything. `servers/sessions.mdx` says the same: the protocol is stateless, state is keyed to the authenticated user.
- The toolsets that are off are the ones with the widest reach: `admin` (users, logs, settings), `raw`, `mail`, `permissions`, `sharing`, `pdf`, `trash` and `bulk`. Text from documents is untrusted data that can carry instructions (decision-4, every prompt and reading tool says so); a tool the assistant can call to widen its own surface is something such text can ask for.
- The default profile has 19 tools of a budget of 20 (decision-34): an enabling tool would take the last slot.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Toolsets are switched on by the operator only. The controller accepted this on 2026-11-23 under the owner's delegation of the choice; the owner did not approve the wording.

- `PAPERLESS_MCP_TOOLSETS`, `PAPERLESS_MCP_READ_ONLY` and `PAPERLESS_MCP_DISABLED_TOOLS` are read at startup and stay the ceiling. No tool and no session rule widens the selection: the server never calls `enable_components`, `disable_components` or `reset_visibility` for a client, and offers no tool that changes the selection.
- The server tells the assistant what is off, what each toolset adds (exactly the tools that remain after read-only mode and disabled tools) and the setting and restart for each way of running it, so that the assistant can ask the user to change it. It does not advertise a toolset that read-only mode, disabled tools or a missing Paperless capability exclude, and shows a capability it could not check as a gap.
- If per-session switching is wanted later, it needs a new decision that supersedes this one and at least: off by default; a separate operator allowlist of the toolsets a session may add (never `admin`, `raw`, `permissions`, `mail`, `sharing` by default); read-only mode and disabled tools always win; a tool annotated as changing the surface so clients ask the user; behaviour defined for the sessionless era (user-scoped rules that outlive the conversation) and for unauthenticated HTTP (refuse); and a place in the tool budget.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The user has to change a setting and restart for each new toolset. That is the cost of keeping the operator in control; the guide makes it one sentence for the assistant to say.
- The guide text is per channel (Claude Code plugin option Toolsets, Cursor plugin none, stdio client config, HTTP process environment, image environment and recreate) and is kept in line with the plugin manifests and the Dockerfile by tests.
- The default profile keeps 19 tools; the guide adds text to the instructions and the overview, bounded by a byte limit a test enforces.
- When per-principal credentials arrive (PLMCP-34 to 37), the capability that decides whether `ai` is listed can differ per request; the guide is built once per start from the startup probe and the text says "not checked" where that probe could not tell. A later task can build it per request.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- A tool `enable_toolset` with per-session rules: rejected for now (see Context): on the sessionless era the switch outlives the session, on unauthenticated HTTP it cannot work at all, it spends the last slot of the tool budget, and injected document text could call it.
- A switch that changes the server-wide selection at run time: rejected, it would let any one client change what every other client may do.
- Telling the assistant nothing and relying on the README: rejected, it is the failure PLMCP-82 reports.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/tools/test_toolset_guide.py`, `tests/tools/test_toolset_budget.py`, `tests/tools/test_selection.py`, `tests/contract/test_instance_reader.py` and `tests/e2e/test_toolset_guide.py` (marker decision-41): the guide names exactly the tools that switching the advertised toolsets on adds, for every combination of selection, read-only mode, disabled tools and capability, with nothing excluded; its size stays under the limits.
- `tests/quality/test_plugin.py` and `tests/quality/test_container.py`: the text matches the plugin manifests (option Toolsets in Claude Code, none in Cursor) and the image (no baked-in selection, recreate to apply).
- `tests/tools/test_registry.py` (marker decision-41): a registry test fails when any code under src calls `enable_components`, `disable_components` or `reset_visibility`; a self-test runs it over a synthetic violator.
