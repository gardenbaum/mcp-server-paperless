---
id: PLMCP-83
title: Record the coverage of the Paperless API and close the rest
status: To Do
assignee: []
created_date: '2026-11-23T12:14:53Z'
updated_date: '2026-11-23T12:22:05Z'
labels: []
dependencies: []
priority: medium
type: chore
ordinal: 71000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The README says the server covers most of the API, but nothing shows endpoint by endpoint what is covered, through which tool, and why not. The owner wants reading and writing, as complete as the API allows. The guard is this task's first step and needs no other task, so it can be built first and the others shrink its list. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The guard comes first: a generated table lists every path and method of the vendored schema with the covering tool, the open task, or an owner-approved exclusion, and the other tasks of this feedback remove their entries from its open list
- [ ] #2 A test fails when an endpoint is neither covered nor listed, also after a schema refresh
- [ ] #3 Query parameters such as ?version= (missing from the schema) are listed by hand
- [ ] #4 Each endpoint left uncovered gets a tool, or an exclusion the owner approved, quoted with its date in the table; next ASN, autocomplete, storage path test, selection data and profile are uncovered today (document suggestions are covered by get_document; profile returns the API token and is denied by decision-25)
- [ ] #5 The README's statement about coverage matches the table
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
