---
id: PLMCP-71
title: Add a privacy page
status: To Do
assignee: []
created_date: '2026-11-23T11:57:03Z'
labels: []
dependencies: []
priority: medium
type: docs
ordinal: 62000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Anthropic's docs ask local connectors for a privacy policy; users also want to know where their documents go. Owner approved on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 PRIVACY.md says what the server reads, writes, stores and logs, and to whom data goes (Paperless, the MCP client and its model, PyPI and the image registry on install, Paperless's own LLM for the ai tools), each statement checked against the code
- [ ] #2 README and docs link it, and the plugin manifest names it as its privacy policy URL once the plugin is merged
- [ ] #3 Tests keep the links valid
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
