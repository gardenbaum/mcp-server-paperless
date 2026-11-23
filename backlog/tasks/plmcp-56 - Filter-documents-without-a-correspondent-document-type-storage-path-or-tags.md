---
id: PLMCP-56
title: Filter documents without a correspondent, document type, storage path or tags
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T00:52:07Z'
updated_date: '2026-11-23T01:18:46Z'
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
- [x] #1 search_documents takes has_correspondent, has_document_type, has_storage_path and has_tags (true: set, false: not set) and sends the matching Paperless isnull filters
- [x] #2 A has_* filter set to false together with the named filter of the same field (e.g. has_correspondent=false with correspondent) is an error that says why; true together with a name is consistent and allowed
- [x] #3 A live e2e test finds a document without a correspondent and without a document type through the new filters
- [x] #4 The tool description names the filters as the way to find documents without a value
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Tests first: tool tests (has_* find and exclude, conflicts), service tests (contradictions), live e2e. 2. search_documents gets has_correspondent, has_document_type, has_storage_path and has_tags, mapped onto the existing DocumentQuery isnull and tagged fields (adapter unchanged). 3. DocumentService.search rejects has_x=false combined with the named filter of the same field. 4. Tool description names the filters; regenerate the surface snapshot deliberately. 5. just check, e2e under flock, mutmut on services.documents.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first: tool tests failed on the unknown has_* arguments; service and tool contradiction tests then passed. Adapter and contract already covered isnull/tagged (tests/contract/test_view_filters.py), so no adapter change. AC #2 reworded after review: only has_x=false with a name is a contradiction; has_x=true with a name is redundant but consistent and allowed (tags_none with has_tags=false is allowed too). Gates: just check green (4653 passed; two macOS-only tests deselected; shard-union quality test only fails when PYTEST_ADDOPTS -k leaks into its subprocess); e2e test_search and test_documents 12 passed on the live stack; mutmut on services.documents: no survivors (some segfault-status mutants, also in untouched functions). Surface snapshot regenerated deliberately (new parameters, description). Review (opus): no Critical; Important was the AC wording (fixed); Minor service test for has_correspondent added.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
search_documents takes has_correspondent, has_document_type, has_storage_path and has_tags (false = without). They map onto the existing DocumentQuery correspondent_isnull, document_type_isnull, storage_path_isnull and tagged fields, so the adapter is unchanged. DocumentService.search rejects has_x=false together with the named filter of the same field (has_tags=false with tags_all or tags_any) with a reason and hint. The tool description names the filters as the way to find documents without a value. Verified: tool, service and live e2e tests, just check, mutation of services.documents, independent review. Surface snapshot regenerated deliberately. Risk: has_x=true with a name is allowed on purpose.
<!-- SECTION:FINAL_SUMMARY:END -->
