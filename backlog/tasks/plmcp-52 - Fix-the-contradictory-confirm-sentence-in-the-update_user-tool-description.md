---
id: PLMCP-52
title: Fix the contradictory confirm sentence in the update_user tool description
status: To Do
assignee: []
created_date: '2026-11-22T21:20:29Z'
updated_date: '2026-11-24T07:06:13Z'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
PLMCP-98 (#111) corrected the description of update_user and the snapshot; open is the test that pins the named cases (criterion 1).
<!-- SECTION:NOTES:END -->
