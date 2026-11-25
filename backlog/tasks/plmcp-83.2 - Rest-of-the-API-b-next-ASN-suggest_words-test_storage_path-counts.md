---
id: PLMCP-83.2
title: 'Rest of the API b: next ASN, suggest_words, test_storage_path, counts'
status: To Do
assignee: []
created_date: '2026-11-25T14:42:17Z'
labels: []
dependencies:
  - PLMCP-83.1
parent_task_id: PLMCP-83
type: feature
ordinal: 108000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second part of PLMCP-83: the next archive serial number, word suggestions, trying a storage path and counts over a selection cannot be read. Design approved by the owner on 2026-11-24 with all recommendations ('Alle wie empfohlen', all as recommended); file design-plmcp-83.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_instance_overview shows the next archive serial number, null if refused
- [ ] #2 suggest_words returns words of the search index that begin with a term, of documents the user sees
- [ ] #3 test_storage_path returns the file path Paperless would use for a document, or says that the template cannot be rendered
- [ ] #4 read_documents(counts=true) counts the selected documents by tag, correspondent, type, storage path and custom field; only visible objects are named, the rest is a number
- [ ] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E over stdio and HTTP
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
