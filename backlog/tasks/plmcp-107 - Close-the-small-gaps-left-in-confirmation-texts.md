---
id: PLMCP-107
title: Close the small gaps left in confirmation texts
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T07:06:16Z'
updated_date: '2026-11-24T11:19:25Z'
labels: []
dependencies: []
priority: medium
ordinal: 82000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Left by the reviews of PLMCP-98 and 75: get_app_config and the result of update_app_config return a service URL with its credentials; a webhook URL with several @ is accepted; quoted() does not escape look-alike quotes as the excerpts of correct_document_text do (names are cut at 60 characters); look-alike letters before the @ of an address are not marked.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Service URLs are returned without their credentials, saying that they have some
- [x] #2 A webhook URL with more than one @ in its authority is refused
- [x] #3 quoted() escapes look-alike double quotes; names in other scripts stay readable
- [x] #4 decision changes are superseding decisions
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
1. Red tests: domain (quoted, both_ends), service (get/update config, webhook), tool (get_app_config output).
2. AppConfig: settings hold the three endpoints without credentials; a new output field names them; a URL whose credentials are not certain is not shown.
3. _webhook refuses more than one @ in the authority, where enabled workflows are checked; switching off, renaming and repairing stay possible.
4. quoted()/both_ends(): look-alike double quotes become escapes, whole escapes count towards the cut; Hebrew stays readable.
5. New decision superseding the sentences of decision-44; surface snapshot of the admin tools; notes.
Not in the acceptance criteria and so only reported: look-alike letters before the @ of an address.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Design: decision-64 supersedes sentences of decision-44 (no accepted decision edited).
- AC1: shown_in_part (output field) names the *_endpoint settings with credentials; the value is the URL without them, or 'a URL that is not shown' where they are not certain. A URL written back as returned drops its credentials; the confirmation says so. Not in the value itself: that would be written back.
- AC2: checked in _webhook, so as the other unclear forms: when an enabled workflow is confirmed, also with confirm=true; a disabled one is checked when it is switched on.
- AC3: same characters as decision-48, also within single quotes; plain double quote, backslash, gershayim between Hebrew letters and the Yiddish double yod stay. An escape counts towards the cut of 60/80 (unchanged), so a long name with such quotes shows less of its text. both_ends() too.
Changed on purpose: test_surface.ambr (all, read-only): get_app_config description and the new output field in both tools' schemas. test_confirmation_text.py: hostile names now start with look-alike quotes, and the helper accepts the escaped form of a stored folder shown whole (process_mail_account). No limit, budget or gate changed.
Red first: 385 targeted tests with the new ones failing before the code; test_confirmation_text 34 of 54 red on the old errors.py. Targeted: 1268 passed, diff-cover 100 %, basedpyright, ruff, import contracts clean. No full suite, E2E or mutation run.
services/pdf*.py on origin/plmcp-79-1-pdf names documents through quoted() and needs no change.
Open, not in the acceptance criteria: look-alike letters before the @ of an address (named in the description only); look-alike single quotes within single quotes; look-alike quotes in a new text destination (folder); get_workflow and list_workflows return a webhook URL with its credentials.

Review of 44e4b57, fixed on top (replaces what the notes above say about the cut, about a URL written back and about the plan):
- I1: quotes with nothing visible or only marks between them count as side by side; U+030B and U+030F are escaped where no letter or digit carries them. M2, M3: gershayim after niqqud and at the bounds of the Hebrew range. M1: U+02EE, U+02BA, U+02BE, U+02BF, U+144A, U+16CC are named in Consequences of decision-64 with the reason. M4, M6, M7 (PLMCP-112), M8 done.
- Ruling a: a URL written back as it was returned stays as stored, also with confirm=true, and the result says so. That makes the confirmation 'remove the credentials of' unreachable: code and its test case removed, replaced by tests that the credentials stay. The sentence fits the budget of update_app_config (test_toolset_budget, test_registry unchanged).
- Ruling b: the cut takes the same 60/80 characters as before; escapes do not count. The server caps no refusal or dialog text, so there is no cap to exceed. Measured worst cases: one name 722 characters (before 62), both ends at 254: 3039, longest refusal of all tools with the fixtures: 8716 (update_user), pinned at 9000. Refusals that name every object (mail rules, e-mailed documents) have no bound, as before.
- Open by ruling c: look-alike single quotes within single quotes, look-alikes in new text destinations, look-alike letters before the @ of an address.
Changed on purpose: test_surface.ambr again (both descriptions, field text); CUT in test_confirmation_text.py counts an escape as one character. The tests of the counted cut are replaced.
Evidence: red first, then 2150 passed, 1 skipped in named modules; diff-cover 100 % against origin/main; ruff, basedpyright, import contracts clean. origin/main (e429b87) merged with -s ours: its tree equals d0f24e9.
<!-- SECTION:NOTES:END -->
