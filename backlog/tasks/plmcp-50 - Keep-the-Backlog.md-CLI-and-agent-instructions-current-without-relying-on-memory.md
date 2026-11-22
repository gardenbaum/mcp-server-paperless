---
id: PLMCP-50
title: >-
  Keep the Backlog.md CLI and agent instructions current without relying on
  memory
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-22T19:28:05Z'
updated_date: '2026-11-22T19:47:02Z'
labels:
  - tooling
dependencies: []
priority: medium
type: chore
ordinal: 50000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Owner, 2026-11-22: always use the latest Backlog.md release, enforced by scripts and CI rather than by the controller remembering it. Today just backlog-install installs backlog.md@latest, but refreshing the managed block of CLAUDE.md and AGENTS.md (backlog agents --update-instructions) is interactive and manual, and nothing notices a new release. Add a non-interactive refresh and a scheduled check that reports drift, following the existing drift-check/drift-issue pattern.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 One just recipe installs the latest Backlog.md and refreshes the managed block of CLAUDE.md and AGENTS.md non-interactively (only that block changes), tested on a temporary copy
- [x] #2 A scheduled workflow compares the latest backlog.md release on npm with the instructions-version header and opens or updates one issue on drift (none when current), with guard tests like the other scheduled jobs
- [x] #3 README and doc-2 describe the routine; the cloud setup script keeps installing the latest release
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
1. Tests first: tests/quality/test_backlog_instructions.py (block parsing, drift, refresh with fake driver, pty driver against a fake menu), test_backlog_workflow.py (guard tests of the workflow and recipes), open-issue --description.
2. scripts/backlog_instructions.py (refresh, check), justfile recipes backlog-update, backlog-drift-check, backlog-drift-issue, .github/workflows/backlog-drift.yml.
3. README and doc-2.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Non-interactive refresh: `backlog agents --update-instructions` (1.57.4) has no flag, so scripts/backlog_instructions.py drives its multi-select menu through a pseudo-terminal (waits for the menu to settle, sends the down arrows, space, enter; once per file, CLAUDE.md index 0, AGENTS.md index 1) and then verifies that the text outside the managed block is byte-identical (restores the file otherwise) and that the header reached the installed version. Tested: unit tests with a fake driver on temp copies, the pty driver against a fake interactive menu, and by hand with the real CLI on a scratch copy (1.55.3 -> 1.57.4, only the header lines changed). Recipes: just backlog-update (install + refresh), backlog-drift-check, backlog-drift-issue; workflow .github/workflows/backlog-drift.yml (Mondays, plus dispatch with open_issue switch), guard tests in tests/quality/test_backlog_workflow.py. open-issue gained an optional --description for the label. mutmut covers only src/ services and paperless, so no mutation run. README Development and doc-2 (standing rulings, via backlog doc update) describe the routine. The owner's cloud setup script already runs npm install -g backlog.md. Not run: the workflow itself (cannot dispatch).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added just backlog-update (latest CLI plus non-interactive refresh of the managed block in CLAUDE.md and AGENTS.md through a pty-driven menu, block-only change enforced), a weekly backlog-drift workflow with one backlog-drift issue, guard tests, README and doc-2 text.
<!-- SECTION:FINAL_SUMMARY:END -->
