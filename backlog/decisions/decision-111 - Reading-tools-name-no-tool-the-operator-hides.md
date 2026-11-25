---
id: decision-111
title: Reading tools name no tool the operator hides
date: '2026-11-25T18:35:25Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-97 says that descriptions and schemas of offered tools may still name a hidden tool, and lists the cases it knew. Counted on main 79e7d1e over HTTP: 15 reading tools named a writing tool (14 known to PLMCP-90.3, and `get_permissions` of PLMCP-83.4).

On 2026-11-25 the owner answered "alles wie von dir empfohlen" (all as you recommend) to "#170 mergen, Kriterium 3 offen lassen und als Folgeaufgabe umsetzen, zuerst die 12 Lese-Tools" (merge #170, leave criterion 3 open and do it as a follow-up task, the 12 reading tools first). After those the owner decides about the rest.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supersedes one sentence of decision-97 for reading tools ("Descriptions and schemas of offered tools still may", with its list of reading tools); the rest of decision-97 stands, also for writing tools.

- **No text of a reading tool in tools/list names a tool the operator hides**: title, description, parameters, output schema. Hidden is what server-wide read-only mode, a read-only toolset or `PAPERLESS_MCP_DISABLED_TOOLS` takes away; of disabled tools this holds for writing ones.
- **A toolset that is only off hides nothing**: the guide names its tools on purpose (decision-41). So `run_saved_view` still names `update_saved_view` while `views` is off, and the default profile is unchanged.
- **Passages** (`tools/passages.py`): per reading tool the words that make sense only while another tool is offered, and what stands instead. A transform at listing leaves them out while every tool of the passage is hidden. The texts in the toolset modules and models stay whole, so with nothing hidden the surface is the same byte for byte.
- **Values are no names**: `empty_trash` as a kind of task (an enum) stays.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A new name in a text of a reading tool needs a passage; a test over every toolset fails without one. Changing a text that has a passage means changing the passage (a test says so).
- Writing tools, results, hints and confirmation texts still name hidden tools; the owner decides about them.
- With read-only mode the texts of 15 tools are shorter.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/tools/test_hidden_names.py` (marker decision-111): every toolset read-only, over HTTP and with stdio's directories, all read-only, read-only mode, each writing tool disabled alone; passages against the texts; nothing else changes; self-test of the check.
- `tests/tools/test_read_only_toolsets.py` (stdio and HTTP, both protocol eras), `tests/e2e/test_stdio.py`, `tests/e2e/test_http.py`; the surface snapshot.
