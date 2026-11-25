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

The worst case of the instructions is longer than the design counted (1440): core, views, pdf and workflows all read-only give 1451. On 2026-11-25 the owner answered "Alles wie empfohlen" (all as recommended) to "Anweisungsgrenze 1451 jetzt, nach 89 auf den gemessenen Wert, höchstens 1474" (instructions limit 1451 now, after 89 the measured value, at most 1474). The wording stays. With the toolset `tasks` (PLMCP-89.1) 1474 is measured.

On 2026-11-25 the owner answered "alles wie von dir empfohlen" (all as you recommend) to "#170 mergen, Kriterium 3 offen lassen und als Folgeaufgabe umsetzen, zuerst die 12 Lese-Tools" (merge #170, leave criterion 3 open and do it as a follow-up task, the 12 reading tools first). What is left after those the owner decides then.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Extends decision-4 and decision-6, supplements decision-20, decision-41 and decision-45; none is superseded.

- **`name:read`** in `PAPERLESS_MCP_TOOLSETS` selects a toolset read-only: none of its tools without `read_only_hint` is offered, tools of one transport (`upload_documents`) included. The same rule as read-only mode, for one toolset. Other toolsets are unchanged.
- **Server-wide read-only mode wins**: the suffixes then change no tool. Disabled tools work on top. A toolset left without a tool counts as none.
- **Nothing is guessed.** An entry that is neither a known name nor a known name with `:read` stops the server. Its suggestion carries `:read` if the entry starts with the name of a toolset (`mailro`, `mail-`) or holds anything but letters; only another plain misspelling (`mial`, `mialro`) may be suggested the bare name (`mail`). So do `all:read` (the message names `PAPERLESS_MCP_READ_ONLY`), `all` beside anything (the message says to list the toolsets) and a toolset both with and without the suffix. A tool without a toolset in the table stops a read-only selection.
- **Saving a download counts as writing**: a read-only `core` or `admin` gets the plain form of its own download tool, with its hints and without directories; the other may still save.
- **What the assistant is told**: a read-only toolset is marked `(read-only)` among those that are on, and the value to set keeps `:read`, so that following it makes nothing writable. In read-only mode nothing is marked (it would say that the others write). With every toolset on, the instructions still name the read-only ones. The instructions, the guide and the overview name no hidden tool. **Descriptions and schemas of offered tools still may**, as they do in read-only mode and for toolsets that are off; the tool itself cannot be called. Known cases (of the texts of every tool, over HTTP):
  - reading tools: `run_saved_view` (`update_saved_view`), `get_saved_view`, `list_users` and `list_groups` (`set_permissions`), `list_trash`, `get_trash_entry` and `get_trashed_file` (`restore_from_trash`; the first also `empty_trash`), `list_share_links` (`create_share_link`, `delete_share_link`), `list_share_bundles` (`rebuild_share_bundle`), `list_workflows` (`create_workflow`), `get_app_config` (`update_app_config`), `list_tasks` (`acknowledge_tasks`, `empty_trash`); and, since PLMCP-87.4, `explain_workflow` (`create_workflow`), the 13th;
  - writing tools that name writing tools of another toolset, such as `restore_from_trash` (13 of them), `update_document`, `reprocess_documents`, `bulk_edit_documents`, `set_permissions`: 18 tools, for example with `core:read,bulk`.
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
