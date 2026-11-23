---
id: PLMCP-73
title: Let Renovate follow new releases in the plugin manifests
status: To Do
assignee: []
created_date: '2026-11-23T11:57:05Z'
labels: []
dependencies: []
priority: medium
type: chore
ordinal: 64000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The directory refuses @latest, so the plugin manifests pin the released version and each release needs a pull request. Owner approved a Renovate rule on 2026-11-23. Depends on PLMCP-68.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 After a PyPI release Renovate proposes one pull request that raises the version and the pin in every plugin manifest
- [ ] #2 The rule is proven against the real files (renovate-config-validator and a dry run or a regex test)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
