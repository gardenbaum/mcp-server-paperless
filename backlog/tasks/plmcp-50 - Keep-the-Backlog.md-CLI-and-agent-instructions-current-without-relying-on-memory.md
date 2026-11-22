---
id: PLMCP-50
title: >-
  Keep the Backlog.md CLI and agent instructions current without relying on
  memory
status: To Do
assignee: []
created_date: '2026-11-22T19:28:05Z'
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
- [ ] #1 One just recipe installs the latest Backlog.md and refreshes the managed block of CLAUDE.md and AGENTS.md non-interactively (only that block changes), tested on a temporary copy
- [ ] #2 A scheduled workflow compares the latest backlog.md release on npm with the instructions-version header and opens or updates one issue on drift (none when current), with guard tests like the other scheduled jobs
- [ ] #3 README and doc-2 describe the routine; the cloud setup script keeps installing the latest release
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
