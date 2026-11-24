---
id: PLMCP-107
title: Close the small gaps left in confirmation texts
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T07:06:16Z'
updated_date: '2026-11-24T11:08:15Z'
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
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
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
<!-- SECTION:NOTES:END -->
