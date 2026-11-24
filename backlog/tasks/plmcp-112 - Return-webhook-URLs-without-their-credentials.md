---
id: PLMCP-112
title: Return webhook URLs without their credentials
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T11:11:57Z'
updated_date: '2026-11-24T12:24:50Z'
labels:
  - safety
dependencies: []
priority: medium
type: bug
ordinal: 91000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
get_workflow, list_workflows, the results of create_workflow and update_workflow and raw_api_get of /api/workflows/ return webhook URLs with user name and password. Found by PLMCP-107 and its review, outside its criteria. A read workflow written back must not lose them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every path that returns a webhook URL leaves out user name and password and says so; writing the shown form back keeps the stored ones
- [x] #2 Adapter, service, tool and contract tests; decision changes are superseding decisions
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
1. domain/urls.py: the form of a URL in results, the empty user name, URLs inside text (logs, error texts); admin_system uses it.
2. WorkflowService: results without credentials (shown_in_part per action, note), write-back by action id, fail closed otherwise, confirmations.
3. raw reader (webhook URLs, logs), get_log, error texts of Paperless.
4. Tests first at domain, adapter, service, tool and contract level; new decision; docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first: test_workflow_credentials 39 of 40 against the old services, test_raw 4, test_errors 2, the get_log test 1 (that code was written in the same step as the tests; red shown by running them on the old source). The contract test pins what Paperless does and was green from the start; live only on GitHub.
Write-back is matched by the action id only (decision-66); anything else that is a shown form is refused.
Beyond the named paths: get_log, /api/logs/ and the error texts of Paperless, which repeat webhook URLs.
Snapshots: test_surface (descriptions of the workflow tools, get_log, paperless_api_get; shown_in_part of an action), one message of test_workflow_messages and one row of test_workflows (a confirmation says "with credentials (not shown)"). e2e test_raw seeds a URL with credentials. No limit, budget or gate changed.
Open: a webhook URL with an @ after its host is returned as "a URL that is not shown" (as in decision-64), reported for confirmation.
Targeted tests only, diff coverage 100 %; no full suite, E2E or mutation run locally.

After the security review of 4934902: one function for values and text (shown_url inside text_without_credentials); loose URL forms, full-width and encoded @, spaces, quotes and several URLs; uncertain text goes from the first URL to the last word with an @; linear (tested); said once per answer (note of get_log, new field, snapshot). Red first: 20 rows of test_urls. Two own test rows with an e-mail address after a URL changed to the ruling to prefer removing more.
PENDING, the owner decides: a webhook URL with an @ after its host is returned as "a URL that is not shown".
<!-- SECTION:NOTES:END -->
