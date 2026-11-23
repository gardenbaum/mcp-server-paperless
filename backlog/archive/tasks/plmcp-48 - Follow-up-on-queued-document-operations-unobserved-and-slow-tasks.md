---
id: PLMCP-48
title: 'Follow up on queued document operations: unobserved and slow tasks'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-22T12:40:00Z'
updated_date: '2026-11-22T15:51:15Z'
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
- [x] #1 Decision or change for the unobserved-task grace
- [x] #2 Decision or change for the 60 s timeout, including tool output if the task id is exposed
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Resolved by the PLMCP-46 review round (#59, #61), no further code: (1) a task the user cannot observe (403 on the task list, never listed, list unreadable) is no longer reported as success after the 2 s grace: TaskWaiter returns Unconfirmed(task_id, reason), and bulk_edit_documents, the version merge and the PDF tools report it (unconfirmed_task_id on DocumentBulkReport and VersionsMerged, StartedTasks.problem=unconfirmed with the task id) and tell the agent to read the task with get_task and the documents again before repeating the call. The 2 s grace stays: without view_paperlesstask nothing better is observable, and probing the effect per change kind would duplicate the read-back the message already asks for. (2) A task still running after 60 s no longer raises UpstreamTimeout: it is the same Unconfirmed result with the task id, so the caller can poll it with get_task. Covered by tests/adapter/test_queued.py, tests/adapter/test_pdf.py and the service tests for bulk documents and versions; live on 3.5.1 by tests/contract/test_bulk.py (restricted users) in e2e-latest.

DoD #1 not applicable: no code changes in this task (the tests are those of #59 and #61).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
No new code: the PLMCP-46 review round answers both limits by reporting an unobservable or slow queued task as unconfirmed with its task id (tool output unconfirmed_task_id / problem=unconfirmed) instead of success or an error. The 2 s grace for users without view_paperlesstask stays, deliberately.
<!-- SECTION:FINAL_SUMMARY:END -->
