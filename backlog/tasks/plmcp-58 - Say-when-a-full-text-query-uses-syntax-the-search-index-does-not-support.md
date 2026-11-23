---
id: PLMCP-58
title: Say when a full-text query uses syntax the search index does not support
status: To Do
assignee: []
created_date: '2026-11-23T00:52:09Z'
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
- [ ] #1 A search whose query uses one of these constructs (field:* presence, lowercase and/or/not between terms, a leading minus) returns a notice that names the construct and the working alternative
- [ ] #2 Queries without these constructs carry no notice, and the search results are unchanged
- [ ] #3 The query parameter description states that NOT, AND and OR must be uppercase and that a leading minus does not exclude
- [ ] #4 Unit tests cover each construct and a clean query
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
