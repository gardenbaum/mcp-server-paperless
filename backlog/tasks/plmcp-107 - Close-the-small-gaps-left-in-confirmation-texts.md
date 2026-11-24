---
id: PLMCP-107
title: Close the small gaps left in confirmation texts
status: To Do
assignee: []
created_date: '2026-11-24T07:06:16Z'
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
- [ ] #1 Service URLs are returned without their credentials, saying that they have some
- [ ] #2 A webhook URL with more than one @ in its authority is refused
- [ ] #3 quoted() escapes look-alike double quotes; names in other scripts stay readable
- [ ] #4 decision changes are superseding decisions
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
