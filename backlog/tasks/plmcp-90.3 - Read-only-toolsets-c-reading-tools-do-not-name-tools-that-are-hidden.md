---
id: PLMCP-90.3
title: 'Read-only toolsets c: reading tools do not name tools that are hidden'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T14:52:30Z'
updated_date: '2026-11-25T18:53:08Z'
labels: []
dependencies:
  - PLMCP-90.1
documentation:
  - >-
    backlog/decisions/decision-97 -
    name-read-in-PAPERLESS_MCP_TOOLSETS-selects-a-toolset-read-only.md
parent_task_id: PLMCP-90
priority: medium
type: feature
ordinal: 109000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Criterion 3 of PLMCP-90.1 says that nothing of a read-only toolset that is hidden is named. The instructions, the guide and the overview hold that; descriptions and schemas of offered tools do not (decision-97 lists the cases). An assistant that reads such a name may promise or try a tool it does not have.

Owner, 2026-11-25 (relayed by the main agent): 'alles wie von dir empfohlen' (all as you recommend), to '#170 mergen, Kriterium 3 offen lassen und als Folgeaufgabe umsetzen, zuerst die 12 Lese-Tools' (merge #170, leave criterion 3 open and do it as a follow-up task, the 12 reading tools first). After those the owner decides about the rest. explain_workflow (PLMCP-87.4) came after the count and is the 13th.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 With a toolset selected read-only, the description and the schemas of its reading tools name no hidden tool: run_saved_view, get_saved_view, list_users, list_groups, list_trash, get_trash_entry, get_trashed_file, list_share_links, list_share_bundles, list_workflows, get_app_config, list_tasks, explain_workflow and get_task_overview
- [x] #2 The passages that depend on another tool are marked as such; what else a description says stays, and with every tool offered the texts are as before
- [ ] #3 A test over every toolset fails when a reading tool names a hidden tool, so that a new tool cannot be missed; stdio, HTTP and both protocol eras are covered
- [ ] #4 The rest (writing tools that name tools of another toolset, and results, hints and confirmation texts) is costed and put to the owner, who decides; criterion 3 of PLMCP-90.1 is ticked only when the owner says it is met
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
1. Failing tests: every reading tool names nothing hidden (each toolset read-only, server-wide, each writing tool disabled alone), texts otherwise as before, transports, E2E.
2. tools/passages.py: per reading tool the passages that name another tool; a transform at listing drops those whose tools the operator hides.
3. New decision, docs, read-only snapshot (shrinks).
4. Cost of the rest for the owner; mutants; draft PR; hosted E2E.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
get_task_overview (PLMCP-89.2) is the 14th reading tool; the owner's 12 were counted before PLMCP-87.4 and 89.2.

Scope taken in from main 79e7d1e: 15 reading tools, not 14: get_permissions (PLMCP-83.4) names set_permissions. The other tools of PLMCP-83 and 89 (suggest_words, test_storage_path, get_system_report) name no hidden tool. empty_trash in list_tasks and get_task_overview is a value of task_type (an enum), no tool name; it stays.

Red first: 37 of the 114 new tool tests failed on main's texts, then green; the later tests (parameters, transports, E2E) were added on finished code. 13 hand mutants: 10 killed at once, 3 survivors closed by a test of parameters, values and lists.
Choice: hidden is what read-only mode, a read-only toolset or a disabled tool takes away; a toolset that is only off is not (the guide names its tools, decision-41), so the default surface is unchanged. Read-only mode and disabled writing tools came along at no extra cost.
Deliberate: the read-only surface snapshot shrinks (15 tools, descriptions only); default and all are unchanged. Guide and instructions limits untouched. decision-111 supersedes one sentence of decision-97 for reading tools.
Cost of the rest for the owner (main 79e7d1e, over HTTP, everything on; nothing built): (1) writing tools: 31 of 63 name another writing tool, 43 references in descriptions, 6 in parameters, 19 in output schemas (one reference = one name per tool and place); the sentences of the descriptions are 40, 7887 of 81839 bytes; 18 of them name a tool of another toolset, the case of core:read,bulk. The passages and the transform exist, so this is the table (about 70 passages or fewer), no new mechanism; the texts of the confirmations are not touched by it. (2) runtime texts: string constants with such a name, docstrings left out: 42 in services (23 files), 11 in domain, 2 in paperless, 10 in tools; they reach results, hints and confirmation texts, and some of the last are descriptions counted above. A service does not know the selection: it would have to be passed in, or cut at the boundary. Confirmation texts would get variants and snapshots. (3) reading tools that name a disabled reading tool: not counted.

Correction: 12 mutants were run, not 13: 9 killed at once, 3 closed.
<!-- SECTION:NOTES:END -->
