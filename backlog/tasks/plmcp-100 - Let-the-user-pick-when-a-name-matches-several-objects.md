---
id: PLMCP-100
title: Let the user pick when a name matches several objects
status: To Do
assignee: []
created_date: '2026-11-23T18:44:55Z'
updated_date: '2026-11-23T18:51:41Z'
labels:
  - tools
dependencies:
  - PLMCP-98
documentation:
  - docs/vendor/fastmcp/docs/servers/elicitation.mdx
priority: low
type: enhancement
ordinal: 76000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An ambiguous name fails with a list of matches and a hint how to name one, usually by id (services/taxonomy.py, principals, views, workflows; a custom field select option by its exact label, services/custom_values.py). The assistant then guesses or asks in free text, which costs a turn and can pick the wrong object on a write. Elicitation offers titled options for exactly this. Select options are in scope, like objects. A dialog on a plain read would interrupt more than it helps, because the assistant can often decide from context.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 On a write with an ambiguous name, a client that declares form elicitation lets the user pick from the titled matches, and the call goes on with the picked one on both protocol eras; the pick must be one of the offered matches
- [ ] #2 Reads, clients without form elicitation, declined or cancelled dialogs, malformed answers and match lists too long for a dialog keep the present error with its list
- [ ] #3 A picked object does not count as a confirmation of a destructive action; where a call needs both, the user is asked both
- [ ] #4 Tests cover pick, decline, cancel, a malformed answer, several ambiguous names in one call and no-handler on both protocol eras
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
