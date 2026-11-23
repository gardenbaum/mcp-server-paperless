---
id: PLMCP-83
title: Close the rest of the Paperless API
status: To Do
assignee: []
created_date: '2026-11-23T12:14:53Z'
updated_date: '2026-11-23T12:24:32Z'
labels: []
dependencies:
  - PLMCP-91
priority: medium
type: chore
ordinal: 71000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The guard (PLMCP-91) shows endpoint by endpoint what is covered. After the other tasks of this feedback, this one closes what is left. The owner wants reading and writing, as complete as the API allows. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each endpoint left uncovered gets a tool, or an exclusion the owner approved, quoted with its date in the table; next ASN, autocomplete, storage path test, selection data and profile are uncovered today (document suggestions are covered by get_document; profile returns the API token and is denied by decision-25)
- [ ] #2 The README's statement about coverage matches the table
- [ ] #3 New operations have contract tests on the fake and Paperless 3.4.2, tool tests and E2E
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
