---
id: PLMCP-72
title: Ship skills for the common jobs in the plugin
status: To Do
assignee: []
created_date: '2026-11-23T11:57:04Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 63000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The plugin installs the server only. Skills for the four built-in prompts (triage_inbox, find_document, annual_review, cleanup_taxonomy) let Claude pick the right workflow by itself. Owner asked for skills on 2026-11-23. Depends on PLMCP-68.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The plugin ships skills for inbox triage, finding a document, annual review and taxonomy cleanup that follow the rules of the server's prompts (proposals first, confirmation before anything final)
- [ ] #2 Each skill names only tools the default profile offers, or says which toolset it needs; a test checks this
- [ ] #3 The plugin still passes claude plugin validate --strict and the directory profile
- [ ] #4 Each skill was run against a throwaway Paperless with invented documents, or the notes say what could not be run
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
