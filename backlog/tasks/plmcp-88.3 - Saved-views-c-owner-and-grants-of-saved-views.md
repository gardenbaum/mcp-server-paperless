---
id: PLMCP-88.3
title: 'Saved views c: owner and grants of saved views'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T10:18:28Z'
updated_date: '2026-11-25T11:41:56Z'
labels:
  - views
dependencies:
  - PLMCP-88.2
parent_task_id: PLMCP-88
priority: medium
type: feature
ordinal: 106000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-88: owner and grants of a saved view cannot be read or set. Shares the single read with PLMCP-81.5. Design approved by the owner on 2026-11-24, all recommendations ('Alle wie empfohlen'); file design-plmcp-88.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 set_permissions takes saved views by name or id with the confirmation of decisions 12 and 19, read one by one
- [ ] #2 get_saved_view shows owner and grants with names
- [ ] #3 The remaining rows of saved_views in docs/api-coverage.toml are covered
- [ ] #4 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover owner, non-owner, superuser and unowned views, with and without confirm
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Stacked on 88.2 and on PLMCP-81.5 (#162), which holds the single read, the time bound and the text for grants kept after a refused owner (the design lets the later one reuse it).
2. Tests first: adapter, contract on fake and live, service, tool, E2E.
3. Kind saved_view in set_permissions; access in get_saved_view; coverage rows, docs, snapshots. decision-91 holds the rule.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented under decision-91 (no own decision; D2 of the design is in it). Stacked on PLMCP-81.5 (#162), whose single read, time bound and text for grants kept after a refused owner are reused.
Red first: adapter 2, contract 5, service (import error and 3), tool 6 red, then green. E2E written before its first hosted run. 11 hand mutants, none survived.
Deviation: no ViewReader.get_access; get_saved_view reads through PermissionReader, so there is one way to read grants. The helpers that show access moved to services/access.py (an import cycle).
Fake: a change grant now lets its holder change the view, as in Paperless.
Deliberate: five tests compare get_saved_view without its new field access.

Review: get_saved_view shows access null where reading it fails with anything but a refusal; an unseen view points to list_saved_views; the confirmation says what a change grant on a view gives (the note was asked for by the main agent, the owner's answer is open; not written into decision-91, which belongs to #164). Views are read one by one and a call stops after the 40 s of PLMCP-81.5. Tests first (7 red); 4 mutants, none survives.

The note of a change grant is decision-96 (proposed, the owner is asked); the number was given by the CLI in a copy of backlog/ outside the repository, because 93 to 95 are taken elsewhere. Full hosted E2E: 45600303375 on 9d33f6f green; 45606044346 on 8629d91 (after it only backlog files, a test marker and the merge of #164).
<!-- SECTION:NOTES:END -->
