---
id: PLMCP-83
title: Record the coverage of the Paperless API and close the rest
status: To Do
assignee: []
created_date: '2026-11-23T12:14:53Z'
labels: []
dependencies: []
priority: medium
type: chore
ordinal: 71000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The README says the server covers most of the API. A guard should show endpoint by endpoint what is covered, through which tool, and why not. Owner asked for it on 2026-11-23 (feedback from daily use), reading and writing as complete as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A generated table lists every path and method of the vendored schema with the covering tool or a reason
- [ ] #2 A test fails when an endpoint is neither covered nor excused, also after a schema refresh
- [ ] #3 Uncovered endpoints that serve a user (for example next ASN, autocomplete, storage path test, selection data, document suggestions, profile) get tools or a recorded reason
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
