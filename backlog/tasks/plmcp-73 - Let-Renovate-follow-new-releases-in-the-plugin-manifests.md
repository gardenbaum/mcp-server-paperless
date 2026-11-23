---
id: PLMCP-73
title: Let Renovate follow new releases in the plugin manifests
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T11:57:05Z'
updated_date: '2026-11-23T12:39:30Z'
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
- [x] #2 The rule is proven against the real files (renovate-config-validator and a dry run or a regex test)
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
1. Red: tests in test_plugin.py apply the renovate rule to the real manifests. 2. Regex custom manager for plugin/.*-plugin/plugin.json, datasource pypi, pep440. 3. renovate-config-validator and a local dry run in a pinned Renovate container on a copy with an older version. 4. docs/development.md; just check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: 3 failed; green: 22 passed. Proof: renovate 44.258.1 config validator --strict passes; a local lookup dry run on a copy with 0.3.1 finds all four places and puts 0.3.2 on the one branch renovate/mcp-server-paperless-0.x. Existing settings unchanged: the Monday schedule, no automerge, prConcurrentLimit 3 apply, so the pull request can come up to a week after a release.

Review fixes. Correction to the note above: the bot's own global configuration automerges (seen in the bodies of PR 54 and 55), so a last packageRule sets automerge false for this package; a 'force' block in the bot's configuration, which this repository cannot see, would still win. Observed in Dependency Dashboard #9: updates wait under Pending Approval until ticked, so the tick is a step of the release. allowedVersions takes only plain versions.

Re-review: 0 Critical, 0 Important. AC 1 stays unchecked until the owner accepts that the PR needs a tick in the Dependency Dashboard first (the bot's own configuration), and until the first real PR. The first note's 'no automerge' means the repository's settings; the rule now sets automerge false for this package (force in the bot's config would still win). Known test gaps: groupName or minimumReleaseAge in an earlier rule, ignorePresets, :disableRenovate.
<!-- SECTION:NOTES:END -->
