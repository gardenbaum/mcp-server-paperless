---
id: PLMCP-58
title: Say when a full-text query uses syntax the search index does not support
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T00:52:09Z'
updated_date: '2026-11-23T01:54:34Z'
labels: []
dependencies: []
type: bug
ordinal: 55000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
User feedback (2026-11-23): "NOT searches silently return 0 hits, so one cannot tell whether there really is nothing or the search does not work." Probing Paperless 3.5.1 (272 documents) showed: uppercase NOT works (`NOT rechnung` 129, `rechnung AND NOT swisscom` 130), but `NOT type:*` and `NOT correspondent:*` return 0, lowercase `rechnung not swisscom` returns 0 (not is a search term), and `rechnung -swisscom` returns 13, the same as `rechnung AND swisscom`, so a leading minus includes instead of excludes. None of these produce an error.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A search whose query uses one of these constructs (field:* presence, lowercase and/or/not between terms, a leading minus) returns a notice that names the construct and the working alternative
- [x] #2 Queries without these constructs carry no notice, and the search results are unchanged
- [x] #3 The query parameter description states that NOT, AND and OR must be uppercase and that a leading minus does not exclude
- [x] #4 Unit tests cover each construct and a clean query
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
1. Tests first: a pure detector (field:* presence, lowercase and/or/not, leading minus; clean queries and quoted phrases give nothing), service test (notice on the result, none when clean), tool test, e2e. 2. DocumentSearchResult gets notices (default empty); DocumentService.search fills it from the query text via a small detector. 3. Fix the query parameter description (uppercase NOT/AND/OR, minus does not exclude, field:* unsupported, has_* filters). 4. Regenerate the surface snapshot deliberately; just check, e2e under lock, mutmut on services.documents.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Detector is a small regex pass (services.documents.query_notices) over the query with quoted phrases removed: field:*, standalone lowercase and/or/not, a term with a leading minus. One notice per construct kind; DocumentSearchResult.notices defaults to empty.

Review (opus): no Critical. Important fixed: a relative range bound such as added:[-2 weeks to -1 week] was read as a leading minus; ranges are now stripped like quoted phrases (clean-query test added). Minor fixed: only a bare field:* counts as presence (title:*voice is silent), lowercase operators next to parentheses are caught. Deferred Minor: only the first minus/presence match is named; a lone lowercase not at the start or end is advised as NOT. Gates: just check green (4692 passed, two macOS-only deselected plus the shard-union test that fails only with PYTEST_ADDOPTS -k), e2e search/documents/views 17 passed live, mutmut services.documents no survivors (segfault-status mutants as before). Surface snapshot regenerated deliberately (notices output field, query description).

Codex P2 findings fixed: field:* before a closing parenthesis, NOT (type:*), is detected, and a minus-prefixed phrase is detected and named with its phrase; quoted phrases and ranges are masked at equal length so positions map back to the query.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
search_documents now returns notices when the full-text query uses syntax the Paperless index silently misreads: field:* presence (points at the has_* filters of PLMCP-56), standalone lowercase and/or/not (write the uppercase operator) and a term with a leading minus (it includes; write NOT term). Detection is a regex pass in services.documents.query_notices over the query without quoted phrases and bracket ranges; clean queries give an empty list and results are unchanged. DocumentSearchResult.notices defaults to empty; the query parameter description states the uppercase rule, the minus and field:*. Verified: unit, service, tool and live e2e tests, just check, mutation, independent review. Risk: plain-language queries with lowercase and/or/not (terms and conditions) also get a notice, which is accurate since they are searched as words.
<!-- SECTION:FINAL_SUMMARY:END -->
