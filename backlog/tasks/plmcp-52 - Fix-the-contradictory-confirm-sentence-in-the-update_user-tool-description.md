---
id: PLMCP-52
title: Fix the contradictory confirm sentence in the update_user tool description
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-22T21:20:29Z'
updated_date: '2026-11-24T21:07:04Z'
labels:
  - tools
dependencies: []
priority: low
type: bug
ordinal: 52000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found while writing the docs (PLMCP-51): the description of update_user (src/paperless_mcp/tools/admin.py) first says a new e-mail address and a rename need confirm, then says 'Renaming and changing names or the e-mail address need none'. Decision-26 says changing or setting the e-mail address and a rename need confirm, while clearing the address and changing first or last name need none. Check what the service does, make the description say exactly that, and regenerate the tool surface snapshot and docs/tools.md deliberately.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The update_user description states the confirm rule once and matches the behaviour and decision-26; a test pins the cases it names (rename, new e-mail, cleared e-mail, first and last name)
- [ ] #2 The surface snapshot change is stated in the PR; docs/tools.md is regenerated
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
1. Compare the description of update_user with AccountAdmin._user_changes and decision-26.
2. Red: a tool test that runs every case the description names (rename, new and changed e-mail, cleared e-mail, first and last name, the access changes) and requires its words exactly once in the description.
3. Green: the description names reactivating too, which needs confirm.
4. Update the surface snapshot; regenerate docs/tools.md.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
PLMCP-98 (#111) corrected the description of update_user and the snapshot; open is the test that pins the named cases (criterion 1).

The description named deactivating but not reactivating, which needs confirm (decision-26 and the service). It now names both. New tool test test_update_user_asks_for_exactly_the_changes_its_description_names: 13 cases (rename, set and changed e-mail, cleared e-mail, first and last name, access changes), each run through the tool and looked up in the description, which states the rule once; red on reactivating before the text changed. Surface snapshot changed deliberately (this description only); docs/tools.md regenerated, unchanged because it holds first sentences only. No decision changed.
<!-- SECTION:NOTES:END -->
