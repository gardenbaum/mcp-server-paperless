---
id: PLMCP-57
title: Offer run_saved_view in the default profile
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T00:52:08Z'
updated_date: '2026-11-23T01:21:40Z'
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
- [x] #1 run_saved_view is in the core toolset and offered by the default profile; the other saved-view tools stay in views
- [x] #2 The default profile stays within the tool budget of 20, enforced by the registry test
- [x] #3 A decision records the move and why; doc-1, the README toolset table and the e2e toolset map agree with it
- [x] #4 list_taxonomy's description points to run_saved_view for running a listed view
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Failing tests: default profile offers run_saved_view and no other view tool; views toolset has five tools, read-only keeps two.
2. TOOL_TOOLSETS maps run_saved_view to core; the tool stays in tools/views.py and takes its tag from the table.
3. decision-34; doc-1 table via backlog doc update; README counts (19 default, core 17, views 5), configuration.md, generated docs/tools.md.
4. list_taxonomy description points to run_saved_view.
5. Regenerate surface snapshots deliberately.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence: just check green apart from the two known macOS-only failures (test_the_menu_driver_reports_a_failing_cli, test_a_socket_is_not_a_file); e2e test_stdio.py + test_views.py: 7 passed; mutation not applicable (mutmut covers services/ and paperless/ only; this change is in tools/).
Independent review (opus): no Critical; one Important fixed (decision-34 body was outside the CLI section markers); three Minor fixed (stale run_saved_view in the views read set of test_stdio and test_visibility, a ragged line in configuration.md).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
run_saved_view moved from the opt-in views toolset to core (decision-34), so the default profile, which already lists saved views through list_taxonomy, can now run them; list_taxonomy says so. The default profile has 19 of its 20 tools; the other five saved-view tools stay in views. doc-1, README counts, configuration.md and the generated tool reference follow; surface snapshots were regenerated deliberately. Leaves one tool of room in the default budget.
<!-- SECTION:FINAL_SUMMARY:END -->
