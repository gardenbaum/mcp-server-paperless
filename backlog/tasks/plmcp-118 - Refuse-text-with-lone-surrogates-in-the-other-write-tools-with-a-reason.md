---
id: PLMCP-118
title: Refuse text with lone surrogates in the other write tools with a reason
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T21:58:48Z'
updated_date: '2026-11-25T17:51:37Z'
labels:
  - tools
dependencies: []
priority: low
type: bug
ordinal: 99000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A lone surrogate cannot be sent, and a tool that passes one on ends with an internal error where the schema does not refuse it. PLMCP-108 refuses it in upload file names, the title of upload_document and version labels. Open: the title of update_document and update_documents, titles of PDF splits, names of taxonomy objects and the like, custom field values, and other free text of the write tools.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every write tool that takes free text refuses lone surrogates with a reason, also over stdio; a sweep over the tool surface shows it
- [ ] #2 Nothing is sent before the refusal
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
1. Red: a sweep over the schemas of every tool of the full surface (HTTP and stdio variants) that puts a lone surrogate at every free text place, nested ones, list items, keys and free-form values too, and wants the reason and no call of a port; the same on the wire of a real stdio server, where the SDK drops such a message unanswered (probed); a self-test with a tool that does not refuse.
2. Green: one middleware that refuses before arguments are validated; over stdio the escapes are taken out of the line before the SDK's parser and the call is marked with the argument they were in.
3. The messages of uploads and version labels become the common one.
<!-- SECTION:PLAN:END -->
