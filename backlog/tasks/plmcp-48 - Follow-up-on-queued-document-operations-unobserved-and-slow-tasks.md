---
id: PLMCP-48
title: 'Follow up on queued document operations: unobserved and slow tasks'
status: To Do
assignee: []
created_date: '2026-11-22T12:40:00Z'
labels: []
dependencies: []
priority: low
type: chore
ordinal: 48000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
PLMCP-46 follows the bulk_update task that paperless-ngx 3.5 queues behind every document operation (paperless/queued.py). Two limits remain: (1) a user without view_paperlesstask cannot be followed, so the adapter only waits a fixed 2 s grace (best effort; the change may land after the call returns for a very busy worker); (2) a task still running after 60 s raises UpstreamTimeout naming the task, although the change is still applied. Decide whether either needs a better answer, e.g. returning the task id to the tools (bulk edit, merge versions) so the caller can poll with get_task, or probing the effect.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Decision or change for the unobserved-task grace
- [ ] #2 Decision or change for the 60 s timeout, including tool output if the task id is exposed
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
