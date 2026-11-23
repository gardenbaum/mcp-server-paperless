---
id: PLMCP-84
title: Keep document content and arguments out of the logs
status: To Do
assignee: []
created_date: '2026-11-23T12:18:23Z'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 72000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The work on the privacy policy (PLMCP-71) found four places where content can reach the log although the rule is that it never does at INFO: resource_boundary in tools/_shared.py logs Paperless's error text at INFO and FastMCP adds a traceback; a failed prompt logs its argument value in a FastMCP traceback; over HTTP a malformed Paperless startup answer is quoted in a uvicorn ERROR traceback; DEBUG over HTTP logs whole answers. Owner approved on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 At INFO and above none of the first three reaches stderr, proven by canary tests over stdio and HTTP, while an error stays diagnosable by type
- [ ] #2 DEBUG over HTTP does not log answers, or the docs and the policy say plainly that it does and why
- [ ] #3 PRIVACY.md and docs/configuration.md match the result
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
