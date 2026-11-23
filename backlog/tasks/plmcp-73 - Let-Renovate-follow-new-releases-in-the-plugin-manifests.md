---
id: PLMCP-73
title: Let Renovate follow new releases in the plugin manifests
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T11:57:05Z'
updated_date: '2026-11-23T12:15:50Z'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: tests in test_plugin.py apply the renovate rule to the real manifests. 2. Regex custom manager for plugin/.*-plugin/plugin.json, datasource pypi, pep440. 3. renovate-config-validator and a local dry run in a pinned Renovate container on a copy with an older version. 4. docs/development.md; just check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: 3 failed; green: 22 passed. Proof: renovate 44.258.1 config validator --strict passes; a local lookup dry run on a copy with 0.3.1 finds all four places and puts 0.3.2 on the one branch renovate/mcp-server-paperless-0.x. Existing settings unchanged: the Monday schedule, no automerge, prConcurrentLimit 3 apply, so the pull request can come up to a week after a release.
<!-- SECTION:NOTES:END -->
