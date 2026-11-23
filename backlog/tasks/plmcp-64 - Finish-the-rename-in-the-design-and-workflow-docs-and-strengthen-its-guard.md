---
id: PLMCP-64
title: Finish the rename in the design and workflow docs and strengthen its guard
status: To Do
assignee: []
created_date: '2026-11-23T09:00:11Z'
labels: []
dependencies: []
type: chore
ordinal: 55000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The late independent review of PLMCP-59 (2026-11-23) found leftovers: doc-1 and doc-2 still name gardenbaum/paperless-mcp (including the PUT merge command), decisions 27, 28 and 32 name the old entrypoint and repository without a superseding decision, the guard in tests/quality/test_docs.py misses shields.io, raw.githubusercontent.com, SSH and API forms and does not cover the Dockerfile, the e2e HTTP test does not run the console script it claims to, docs/http.md does not connect the old command aliases with the PyPI name clash.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 No doc or Dockerfile names the old repository
- [ ] #2 The guard catches shields.io, raw, SSH and API forms with a self-test
- [ ] #3 A decision records the new entrypoint and Trusted Publisher repository
- [ ] #4 The e2e HTTP test starts the mcp-server-paperless-http console script
- [ ] #5 docs/http.md says not to run uvx paperless-mcp
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
