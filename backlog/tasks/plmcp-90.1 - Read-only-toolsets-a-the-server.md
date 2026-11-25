---
id: PLMCP-90.1
title: 'Read-only toolsets a: the server'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T12:22:10Z'
updated_date: '2026-11-25T12:52:07Z'
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
- [ ] #1 An entry name:read in PAPERLESS_MCP_TOOLSETS offers no tool of that toolset without the read-only hint, local tools and saving downloads included; other toolsets are unchanged
- [ ] #2 all:read, all with exceptions, an unknown suffix, a toolset both with and without suffix and an unknown name stop the server with a message that says what to write; server-wide read-only mode wins
- [ ] #3 What the assistant is told marks a read-only toolset and the value to set keeps :read; nothing of it is named that is hidden; prompts that need a hidden tool are not offered
- [ ] #4 A decision records the setting and how it extends decisions 4 and 6; README, configuration, container docs and server.json say it
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests over stdio and HTTP on both protocol eras and E2E cover mixed selections, downloads, prompts and the error messages
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
<!-- SECTION:NOTES:END -->
