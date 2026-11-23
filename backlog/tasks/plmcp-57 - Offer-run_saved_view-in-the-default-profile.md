---
id: PLMCP-57
title: Offer run_saved_view in the default profile
status: To Do
assignee: []
created_date: '2026-11-23T00:52:08Z'
labels: []
dependencies: []
type: feature
ordinal: 54000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
User feedback (2026-11-23): "The most helpful thing would be if the server could run saved views directly." run_saved_view exists but sits in the views toolset, which the default profile (core, ai) leaves out; list_taxonomy in core lists saved views, so agents see the views but cannot run them. The default profile has 18 tools against a budget of 20 (doc-1 section 6). Moving one tool between toolsets changes the approved design (doc-1 lists run_saved_view under views), so a decision records it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 run_saved_view is in the core toolset and offered by the default profile; the other saved-view tools stay in views
- [ ] #2 The default profile stays within the tool budget of 20, enforced by the registry test
- [ ] #3 A decision records the move and why; doc-1, the README toolset table and the e2e toolset map agree with it
- [ ] #4 list_taxonomy's description points to run_saved_view for running a listed view
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
