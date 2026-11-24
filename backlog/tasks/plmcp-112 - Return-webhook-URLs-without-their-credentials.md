---
id: PLMCP-112
title: Return webhook URLs without their credentials
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T11:11:57Z'
updated_date: '2026-11-24T13:18:31Z'
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

Second independent review (of 89deffd): I1, I2, M2 to M10 fixed, M1 (copies) taken in. Red first: 65 failing rows before the fix (plus test_urls, which failed to import lines_without_credentials), 9 for M1, then green. The old without_credentials of domain/errors.py is gone: one stricter function in domain/urls.py for results, confirmations, refusals and text. Also found: a refusal of a webhook repeated an encoded @ host (https://s3cret%40h). Own tests changed deliberately: log lines of two tests carry the time Paperless writes; note of an empty user name without an id and of replaced credentials; id 99 moved. Snapshot: one sentence in create_workflow (test_surface); docs/tools.md unchanged after just docs-tools. No limit, budget or gate changed.
Owner rulings of 2026-11-24 (no longer pending): a webhook URL with an @ after its host is not shown; the stricter rule also applies to service URLs with a full-width @ or %40; in log lines/errors the text between a URL and a later e-mail address may go. M1: option A approved ("Wie von dir empfohlen."): a copy is announced, never copied, the other workflow is not named, no further read.
Targeted tests only: 2417 passed, diff coverage 100 %, ruff, basedpyright of the changed files, hygiene, import contracts. tests/quality/test_backlog_instructions.py::test_the_menu_driver_reports_a_failing_cli failed locally with EIO on a pseudo-terminal (code untouched).

Delta review of 2650c31, after merging main (bdf8bf9): red first (28 failing rows), then green. get_log reads 1000 lines, redacts and shows the last ones asked for; the undated head of a cut log is not shown up to its last line with an @ (get_log, /api/logs/). Own hint for an unknown action id; anchored spelling comparison (linear, tested); an empty user name alone is not called credentials in workflows; *_url keys, and url in /api/status/, are redacted (the vendored docs do not say whether redis_url holds a password; the status tool does not return it). Own tests changed: the record tests pass from_start, one raw log text carries a date. 1914 targeted tests passed, diff coverage 100 %. No snapshot, limit, budget or gate changed.

Last review of e5f1a5f: from_start removed, the rule for the undated head of a log always applies (a short log too; replaces what the note above says on tests with from_start: record tests now start with a dated line). The id check runs before the URL and the headers are looked at, so one row (id 99) of test_a_hidden_header_without_a_stored_value_to_keep_is_refused now expects the refusal of the id instead of the header. 5 rows red first.
<!-- SECTION:NOTES:END -->
