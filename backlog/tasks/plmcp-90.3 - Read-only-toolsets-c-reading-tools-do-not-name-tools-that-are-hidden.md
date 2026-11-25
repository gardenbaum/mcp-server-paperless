---
id: PLMCP-90.3
title: 'Read-only toolsets c: reading tools do not name tools that are hidden'
status: To Do
assignee: []
created_date: '2026-11-25T14:52:30Z'
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
- [ ] #1 With a toolset selected read-only, the description and the schemas of its reading tools name no hidden tool: run_saved_view, get_saved_view, list_users, list_groups, list_trash, get_trash_entry, get_trashed_file, list_share_links, list_share_bundles, list_workflows, get_app_config, list_tasks and explain_workflow
- [ ] #2 The passages that depend on another tool are marked as such; what else a description says stays, and with every tool offered the texts are as before
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
