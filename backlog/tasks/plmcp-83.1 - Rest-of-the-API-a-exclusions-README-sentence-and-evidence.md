---
id: PLMCP-83.1
title: 'Rest of the API a: exclusions, README sentence and evidence'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T14:42:15Z'
updated_date: '2026-11-25T14:48:11Z'
labels: []
dependencies: []
parent_task_id: PLMCP-83
type: chore
ordinal: 107000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-83: the operations and fields the owner left out stand open in the coverage table, and the README does not say how far the coverage goes. Design approved by the owner on 2026-11-24 with all recommendations ('Alle wie empfohlen', all as recommended); file design-plmcp-83.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every operation and field the owner left out stands as an exclusion with quote and date in docs/api-coverage.toml; what parts b to d and other tasks build stays open
- [ ] #2 The README's statement about coverage is tested against the table
- [ ] #3 E2E tests on Paperless 3.5.1 hold the evidence of the left-out forms (JSON to the e-mail of one document, body of the bundle rebuild, data type of a custom field); they are no ports, so the fake has nothing to hold
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
1. Test first: the README sentence is the one the table allows (no open operation: covered or owner-approved exclusion; else: or still open). 2. Decision with the owner's words; exclusion rows; what parts b to d build stays open. 3. Evidence in E2E (tests/e2e/test_left_out_operations.py): the excluded operations are no ports, so no fake can hold them. 4. docs/api-coverage.md regenerated.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first: statement tests (import error), README test, then green; the table (30 operations and 186 fields open before) now lists 24 operations and 177 fields as exclusions, 6 operations and 9 fields stay open for parts b to d, exactly the design's count. E2E evidence written before its first hosted run. decision-99 (the CLI gave 98 first, which is another task's; removed). Deviation: the design says contract tests for the evidence; the left-out operations are no ports, so no fake can hold them and they are E2E on 3.5.1. DELETE of the configuration is not kept as a test: the stack's one row is shared; read in the source (a new row is made at the next use, every setting and key null).
<!-- SECTION:NOTES:END -->
