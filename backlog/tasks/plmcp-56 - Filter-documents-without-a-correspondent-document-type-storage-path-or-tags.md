---
id: PLMCP-56
title: Filter documents without a correspondent, document type, storage path or tags
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T00:52:07Z'
updated_date: '2026-11-23T00:55:24Z'
labels: []
dependencies: []
type: feature
ordinal: 53000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
User feedback (2026-11-23): an agent could not find documents that lack a document type or a correspondent. search_documents has no such filter, and the full-text workaround `NOT type:*` / `NOT correspondent:*` returns 0 on Paperless 3.5.1 without any error, so the agent cannot tell that the search did not work. DocumentQuery already carries correspondent_isnull, document_type_isnull, storage_path_isnull and tagged for saved views; the search tool does not expose them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 search_documents takes has_correspondent, has_document_type, has_storage_path and has_tags (true: set, false: not set) and sends the matching Paperless isnull filters
- [ ] #2 A has_* filter together with the named filter of the same field (e.g. has_correspondent=false with correspondent) is an error that says why
- [ ] #3 A live e2e test finds a document without a correspondent and without a document type through the new filters
- [ ] #4 The tool description names the filters as the way to find documents without a value
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
1. Tests first: tool tests (has_* find and exclude, conflicts), service tests (contradictions), live e2e. 2. search_documents gets has_correspondent, has_document_type, has_storage_path and has_tags, mapped onto the existing DocumentQuery isnull and tagged fields (adapter unchanged). 3. DocumentService.search rejects has_x=false combined with the named filter of the same field. 4. Tool description names the filters; regenerate the surface snapshot deliberately. 5. just check, e2e under flock, mutmut on services.documents.
<!-- SECTION:PLAN:END -->
