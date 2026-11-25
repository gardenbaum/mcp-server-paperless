---
id: PLMCP-87.4
title: 'Workflows d: explain_workflow'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:43:07Z'
updated_date: '2026-11-25T10:37:49Z'
labels:
  - workflows
dependencies:
  - PLMCP-87.1
parent_task_id: PLMCP-87
priority: medium
type: feature
ordinal: 102000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth part of PLMCP-87: why a workflow did not run on a document can only be guessed; Paperless keeps no readable record of runs. Design approved by the owner on 2026-11-24, all recommendations (file design-plmcp-87.md of the main agent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 explain_workflow compares settings, document, history, consume task and logs and says what is inferred and what cannot be known
- [x] #2 Log lines show no text or names of other documents
- [x] #3 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover matching, not matching and restricted users
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe P6 (done). 2. log_records helper, explanation service, port window, tool, coverage, decision-88, docs. 3. Full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24): 'Alle wie empfohlen' (question in decision-81), here 7a, 8a. Probe P6: run 45585312563, job 139105284889 (results in decision-88). The production code of this part was written before its tests (not red first); the tests were then proven with 39 hand mutants (4 survived first, closed). Coverage 100 %.
Shared helper for 81d: services/log_records.py.

Full hosted E2E 45588369857 on 87f27da: e2e test_workflows 11 (explain_workflow for a run, a non-matching and a later workflow, restricted user), contract, stdio and parts green. Afterwards: records count only with logger and whole text (review of 81d), tests red first; log_records.py is a copy of 81d's, which owns it.

Independent review of #161 (2cb483c): H1, H2, M1 to M7, L1 to L4, L6, L7 fixed, tests first (red). did_run is likely, never yes. 39 further hand mutants: one survived, equivalent (other errors are raised again). L5, L8 and L9 were not described. log_records.py and its test are those of PLMCP-81.4 (9d55a71).
<!-- SECTION:NOTES:END -->
