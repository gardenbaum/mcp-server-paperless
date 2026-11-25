---
id: decision-97
title: name:read in PAPERLESS_MCP_TOOLSETS selects a toolset read-only
date: '2026-11-25T12:22:19Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-90: read-only mode (decision-4, decision-6) holds for the whole server, so reading mail rules or workflows meant offering their writing tools too, or giving up every write.

On 2026-11-24 the owner approved the design: asked in a question dialog "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?" (do you approve the drafts with all recommended options, with the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010), the owner chose "Alle wie empfohlen" (all as recommended). That covers the suffix instead of a variable, the refusal of `all` with exceptions, downloads that only attach, the Cursor plugin left as it is, the limits and two parts.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Extends decision-4 and decision-6, supplements decision-20, decision-41 and decision-45; none is superseded.

- **`name:read`** in `PAPERLESS_MCP_TOOLSETS` selects a toolset read-only: none of its tools without `read_only_hint` is offered, tools of one transport (`upload_documents`) included. The same rule as read-only mode, for one toolset. Other toolsets are unchanged.
- **Server-wide read-only mode wins**: the suffixes then change no tool. Disabled tools work on top. A toolset left without a tool counts as none.
- **Nothing is guessed.** An entry that is neither a known name nor a known name with `:read` stops the server, and a suggestion for it always carries `:read`. So do `all:read` (the message names `PAPERLESS_MCP_READ_ONLY`), `all` beside anything (the message says to list the toolsets) and a toolset both with and without the suffix. A tool without a toolset in the table stops a read-only selection.
- **Saving a download counts as writing**: a read-only `core` or `admin` gets the plain form of its own download tool, with its hints and without directories; the other may still save.
- **What the assistant is told**: a read-only toolset is marked `(read-only)` among those that are on, and the value to set keeps `:read`, so that following it makes nothing writable. In read-only mode nothing is marked (it would say that the others write). With every toolset on, the instructions still name the read-only ones. Hidden tools are named nowhere.
- **Prompts** follow decision-20: one that needs a hidden tool is not offered.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Releases up to 0.4.0 refuse to start on the suffix instead of offering the writes. The Claude Code plugin may name it only with a pin that knows it (PLMCP-90.2).
- It is about the hint of our tools: what the Paperless token may do stays, and a reading tool can cost money (`ai`) or show much (`raw`).
- `run_saved_view` is in `core` (decision-34): `views:read` does not touch it. `test_mail_account` contacts a server, so `mail:read` hides it.
- `all` cannot have exceptions: whoever runs `all` lists the toolsets, and does not get new ones by itself.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/tools/test_selection.py` (property over any selection, mode and disabled tools; every refusal), `tests/test_settings.py`, `tests/test_cli.py`.
- `tests/tools/test_visibility.py`: every toolset once read-only, local tools and saving downloads included; hidden tools are refused and nothing is written. `tests/tools/test_downloads.py`: the four combinations of `core` and `admin` on both transports. `tests/tools/test_read_only_toolsets.py`: both transports, both protocol eras. `tests/tools/test_prompts.py`.
- `tests/guide_oracle.py` behind `tests/tools/test_toolset_guide.py`, `tests/contract/test_instance_reader.py` (fake and Paperless 3.5.1) and `tests/e2e/test_toolset_guide.py`; sizes in `tests/tools/test_toolset_budget.py`.
- `tests/e2e/test_stdio.py`, `tests/e2e/test_http.py`; `scripts/image_smoke.sh`, `scripts/image_stdio_smoke.sh`; `tests/quality/test_docs.py`.
