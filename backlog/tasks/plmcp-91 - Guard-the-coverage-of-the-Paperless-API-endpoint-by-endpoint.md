---
id: PLMCP-91
title: Guard the coverage of the Paperless API endpoint by endpoint
status: To Do
assignee: []
created_date: '2026-11-23T12:24:30Z'
labels: []
dependencies: []
priority: high
type: chore
ordinal: 61500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Nothing shows endpoint by endpoint what the server covers, through which tool, and why not. A guard built first shows every following task what is still missing. Split from PLMCP-83; the owner put it first on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A generated table lists every path and method of the vendored schema with the covering tool, the open task, or an owner-approved exclusion quoted with its date
- [ ] #2 A test fails when an endpoint is neither covered nor listed, also after a schema refresh; it derives coverage from the code or the contract tests, not from a hand-written claim
- [ ] #3 Query parameters missing from the schema, such as ?version=, are listed by hand
- [ ] #4 The entries left open name their task, and closing a task removes them
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
