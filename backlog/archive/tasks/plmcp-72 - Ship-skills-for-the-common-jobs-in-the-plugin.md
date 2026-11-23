---
id: PLMCP-72
title: Ship skills for the common jobs in the plugin
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T11:57:04Z'
updated_date: '2026-11-23T15:33:40Z'
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
- [x] #1 The plugin ships skills for inbox triage, finding a document, annual review and taxonomy cleanup that follow the rules of the server's prompts (proposals first, confirmation before anything final)
- [x] #2 Each skill names only tools the default profile offers, or says which toolset it needs; a test checks this
- [x] #3 The plugin still passes claude plugin validate --strict and the directory profile
- [x] #4 Each skill was run against a throwaway Paperless with invented documents, or the notes say what could not be run
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: tests for plugin/skills (frontmatter, names, tools of the default profile, steps and confirmation rule equal to the prompts). 2. Four skills whose steps are the prompts' own text. 3. READMEs. 4. validate --strict --profile directory. 5. Run the tool sequences with an MCP client against a throwaway Paperless (project plmcp72, port 8071) and the released package.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Skills repeat the prompts' text (test compares them). Run: each skill's tool sequence was called through an MCP client with the released 0.3.2 against a throwaway Paperless 3.4.2 (project plmcp72) with three invented documents; every named tool exists and was called; renaming to an existing name and deleting without confirm were refused. Not run: no model followed a skill (no login used), so whether Claude or Cursor picks the right skill, waits for the yes, and answers well is untested; Cursor loading is checked against its docs only. Limits: skills reach installed users only with a new plugin version (the pinned package version); chat on claude.ai loads skills without the local server.

Review fixes: every skill says document text is data, never instructions; the test compares description and whole body exactly. Based on plmcp-73 (merged in). Inherited from the prompts, not changed here: the cleanup merge step names no storage path, and the prompts do not say that document text is data.

Re-review: 0 Critical, 0 Important. AC 4: the tool sequences ran against a throwaway Paperless; no model ran the skills. Inherited from the prompts and left for a release: a consistent weakening of prompt and skill together is not caught, the merge step names no storage paths, and prompts and get_document_content do not say that document text is data. Owner kept the paperless- prefix on 2026-11-23.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Four skills in plugin/skills (inbox triage, find a document, annual review, taxonomy cleanup) carry the server prompts' own steps and confirmation rule and treat document text as data. Tests compare the whole text. They reach installed users with the next plugin version. DoD 2: one known macOS-only local failure, CI green.
<!-- SECTION:FINAL_SUMMARY:END -->
