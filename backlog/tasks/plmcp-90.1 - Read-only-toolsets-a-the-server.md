---
id: PLMCP-90.1
title: 'Read-only toolsets a: the server'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T12:22:10Z'
updated_date: '2026-11-25T16:27:20Z'
labels: []
dependencies: []
parent_task_id: PLMCP-90
priority: medium
type: feature
ordinal: 107000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-90: reading mail rules, workflows or views means either offering their write tools too or giving up every write. Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen', all as recommended); file design-plmcp-90.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An entry name:read in PAPERLESS_MCP_TOOLSETS offers no tool of that toolset without the read-only hint, local tools and saving downloads included; other toolsets are unchanged
- [x] #2 all:read, all with exceptions, an unknown suffix, a toolset both with and without suffix and an unknown name stop the server with a message that says what to write; server-wide read-only mode wins
- [ ] #3 What the assistant is told marks a read-only toolset and the value to set keeps :read; nothing of it is named that is hidden; prompts that need a hidden tool are not offered
- [x] #4 A decision records the setting and how it extends decisions 4 and 6; README, configuration, container docs and server.json say it
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests over stdio and HTTP on both protocol eras and E2E cover mixed selections, downloads, prompts and the error messages
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Tests first: settings and selection (refusals, property), guide, registry/visibility, downloads, transports, CLI, contract, E2E.
2. selection.py: entries name[:read]; read-only set; hidden_tools; guide text.
3. server.py: saving variant per download tool and sink per service.
4. Decision, docs, server.json, measured guide limit (+17).
5. Mutants in an archive copy, draft PR, full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner's words (2026-11-24, question dialog 'Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?'): 'Alle wie empfohlen' (all as recommended).

Red first: test_selection failed to import; 58 tests red in settings, CLI, visibility, downloads, transports, prompts, guide (contract), budget and docs; then green. E2E written before its first hosted run.
Limits: guide 3011 to 3028 (+17, as approved). Instructions 1450 to 1451: the design's 1440 (core alone, read-only) is not the worst case. A read-only toolset whose line among those that are off is short (views, pdf, workflows) makes the text longer; the budget test now searches for that. 1451 is measured and below the approved 1465, but that was approved for once PLMCP-89 is in; with 89 the worst case is computed at 1474, above 1465: put to the owner.
Deliberate: no test or snapshot weakened; in the budget test tools of the selected toolset itself are no longer disabled (it changes no text). hidden_tools takes the read-only toolsets and the table as required arguments. Smoke tests of the two images start with core:read.
Part b (PLMCP-90.2) waits for a release that contains this and a pin of it; nothing is prepared.

30 hand mutants: 28 killed at once, 1 survivor killed by a new case (bulk-ro), 1 equivalent (another reading of the suffix: anything but :read was refused before).

Owner, 2026-11-25 (relayed by the main agent): 'Alles wie empfohlen' (all as recommended), to 'Anweisungsgrenze 1451 jetzt, nach 89 auf den gemessenen Wert, höchstens 1474' (instructions limit 1451 now, after 89 the measured value, at most 1474); the wording stays.

Review of #170 (3ce7a53), relayed by the main agent: F2, what starts with a toolset's name is suggested with :read (12 tests red first); F3, configuration page; F4, hidden calls must be 'Unknown tool' (a sharper assertion, green at once). Mutant mark=True in server.py is killed by the guide oracle (61 tests of test_toolset_guide, 4 of test_instance_reader). 6 mutants of the new suggestion rule, 3 survivors closed.
AC 1, 2, 4 proven. AC 3 is not: the instructions, guide and overview name nothing hidden, but descriptions and schemas of offered tools still name hidden tools (decision-97 lists the known cases); the owner decides, the criterion is unchanged. AC 5 stays open: its live part was green on a52f7b2 (E2E 45617913750, except e2e-imap, red on main too), not yet on the final head.
DoD stays open: just check was not run as a whole, CI and the hosted E2E of the final head are missing.

Owner, 2026-11-25 (relayed by the main agent): 'alles wie von dir empfohlen' (all as you recommend), to '#170 mergen, Kriterium 3 offen lassen und als Folgeaufgabe umsetzen, zuerst die 12 Lese-Tools' (merge #170, leave criterion 3 open and do it as a follow-up task, the 12 reading tools first): PLMCP-90.3. Criterion 3 stays unticked.
Merged main de956f5: guide 3118 to 3135 (+17), instructions 1474 measured with the toolset tasks, exactly the approved maximum; no byte is left for PLMCP-89.2 and later toolset texts.

AC 5: full hosted E2E 45639073065 on 2bec926 green in all ten jobs (live contract, stdio and HTTP, e2e-imap included). The head after it only adds the merge of main 58c792a (guide 3175, instructions 1474 unchanged, 391 targeted tests) and records; the full E2E was not repeated on it.

Cleanup 2026-11-25, criteria read literally (every item at every named level). Merged as 205e78f (#170). Runs: full hosted E2E 45639073065 on 2bec926 green in all ten jobs, CI 45646896491 of 205e78f green. AC 3 stays open (PLMCP-90.3). AC 5, missing: contract tests of downloads, prompts and the error messages of a wrong selection; E2E of the downloads of a read-only toolset. Proven: mixed selections at every level; downloads and prompts in tool tests over stdio and HTTP on both eras; prompts and error messages in E2E. DoD 2 stays open: no local run of the whole just check is recorded, and the check jobs of the last head of #170 were cancelled by its merge.

Read literally, AC 5 also misses the error messages of a wrong selection in tool tests over stdio and HTTP on both protocol eras. The missing tests are in PLMCP-120.
<!-- SECTION:NOTES:END -->
