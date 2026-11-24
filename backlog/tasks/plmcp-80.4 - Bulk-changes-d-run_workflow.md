---
id: PLMCP-80.4
title: 'Bulk changes d: run_workflow'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:35Z'
updated_date: '2026-11-24T21:13:44Z'
labels:
  - bulk
dependencies:
  - PLMCP-80.2
parent_task_id: PLMCP-80
priority: medium
type: feature
ordinal: 95000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth part of PLMCP-80: Paperless 3.5.1 can run a workflow with a manual trigger on chosen documents, this server cannot. Design approved by the owner on 2026-11-24 (decisions 1 to 20 as recommended); file design-plmcp-80.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 run_workflow runs a workflow with a manual trigger on named documents after a confirmation that says what the workflow does
- [x] #2 Contract, tool and E2E tests cover a restricted user, an unusable workflow and a partial failure
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read the 3.5.1 source of run_workflow (bulk_edit, tasks, actions, matching).
2. Tests first, red then green, bottom up: adapter (WorkflowWriter.run_workflow, TaskWaiter row), contract on fake and live, service (services/workflow_runs.py), tool (run_workflow in workflows, opt-in), E2E.
3. Selection arguments move from tools/bulk.py to tools/_shared.py; services/selection.py and the action texts of decision-21 are reused.
4. Registry, budgets (guide limit 2928 to 2943, owner-approved), coverage table, docs, snapshots, decision-75.
5. Draft PR, one full hosted E2E on the finished head.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval (2026-11-24): 'Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits.' Here: 11a (tool run_workflow in the toolset workflows), 14 (guide limit to at most 2943).

Implementation notes (2026-11-24): port WorkflowWriter.run_workflow, adapter on bulk_edit, services/workflow_runs.py, tool run_workflow in workflows, decision-75 (74 belongs to PLMCP-79.5; the CLI proposed 74, so a second decision was created and the first removed again). Red first at adapter, contract (fake), service and tool level; E2E written before its first run on GitHub. 34 hand mutants of services/workflow_runs.py, the new parts of services/workflows.py and paperless/workflows.py: all killed. Coverage of workflow_runs.py and paperless/workflows.py 100 % (branches too).
Deliberate changes: OVERVIEW_GUIDE_LIMIT 2928 to 2943 (approved); surface snapshot only insertions; selection arguments moved to tools/_shared.py; TaskWaiter.ended returns the task; run_workflow in the test tables of trashing tools, destinations and confirm calls; e2e_ci ALIASES.
Beyond the design: the confirmation also shows the values assignments and removals write (the design named the fields' kinds only); current values and owners are bound (decision-71); ownership is checked beforehand where the user may be read (may_delete asks for delete_document, which a run does not need, so get_signed_in and the rows' owners are used); a failure on some documents is a result, on all an error.
Local runs: targeted only; tests/tools, tests/quality and others green except test_backlog_instructions::test_the_menu_driver_reports_a_failing_cli, a pty error of this machine in a file this task does not touch.

Hosted evidence on e6549f1: full E2E run 45464274162 green (four shards, postgres, office, duplicates), so the live contract and E2E tests of run_workflow passed on Paperless 3.5.1 at their first run; PR tier of CI run 45464288047 green, 10672 tests passed on 3.14. Its check jobs were red for other reasons: one uncovered line of PLMCP-80.3 (bulk_updates.py 968, fixed in its branch by fb4b56e, merged here) and a network error of the Go checksum server on 3.15. Then only the base branch and backlog notes were merged; no source file changed since.

Review round 1 (F1 to F11), tests first: manual triggers count only the filters Paperless reads; tags that come along named, not refused (Paperless takes them whatever the caller asks); actions before documents, far-reaching first; AI settings, select labels, linked titles in words; only the custom fields Paperless writes; dry_run stays true without a match; texts of a run; missing change_document told beforehand (SignedIn.permissions); the plain-text 400 is 'not known'. tests/adapter/schema_guard.py: one documented deviation more (bulk_edit 400, text/html). Surface snapshot: two changed lines of run_workflow. Sizes measured (100 documents, six actions): 11,917 ordinary, 42,821 hostile; previews 7,164 and 24,564. 29 further hand mutants, all killed after four tests more. F12 left.

Review round 2: each clause has its step, the list of tags is read anew once per call where tags are assigned or removed, linked ids in hundreds, texts of the write tools say run_workflow, tests for N4. Sizes now 12,020 and 42,924 (previews unchanged). Correction of round 1: 25 hand mutants (4 survived and were killed by new tests), not 29. The refusal above 100,000 characters is with the owner and not built.

Owner decisions of 2026-11-24 ('Alles wie von dir empfohlen.'): failing e-mails and webhooks do not fail the run; a partial failure is a result; contents of e-mails and webhooks are bound, not shown; tags that come along are named, not refused; a confirmation above 100,000 characters is refused, also with confirm=true, never cut (built, tests first). Hosted on 1b19291: CI 45478434614 green; full E2E 45478418857: shard 1/4 was cancelled by the 25-minute guard at 96 % without a failed test (main had the same, PLMCP-115), the others green. A full E2E on the final head follows once main has six shards.

Review round 3 (L1 to L7, N4 rest, N7), tests first: the 100,000 count the whole text with its hint, the tool text names the limit, the preview skips a clause that does not fit and counts steps, hints for a single long step and the shortest document, stored destinations name what to do. New domain function confirmation_length. Surface snapshot: two descriptions of run_workflow. Merged main with PLMCP-79.5.
<!-- SECTION:NOTES:END -->
