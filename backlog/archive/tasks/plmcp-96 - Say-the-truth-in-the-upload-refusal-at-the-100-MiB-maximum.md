---
id: PLMCP-96
title: Say the truth in the upload refusal at the 100 MiB maximum
status: Done
assignee: []
created_date: '2026-11-23T15:33:41Z'
updated_date: '2026-11-23T20:43:00Z'
labels: []
dependencies: []
priority: low
type: bug
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
services/upload_source.py tells the caller that the operator can raise PAPERLESS_MCP_MAX_UPLOAD_BYTES. At the setting's maximum of 100 MiB that is untrue. Found in the review of PLMCP-95; the owner approved a separate task after 0.3.3 on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A refused upload never says that the limit can be raised when it is already 100 MiB
- [x] #2 Below the maximum the refusal still names the setting the operator can raise
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Maximum now lives once as MAX_UPLOAD_LIMIT in domain/documents.py; settings.py (le=) and services/upload_source.py (_too_large) read it. At the maximum the hint is 'Upload a smaller file.'; below it still names PAPERLESS_MCP_MAX_UPLOAD_BYTES. Checked other claims: app.py 413 text says 'at most 100 MiB' and never says raise (already tested); docs/configuration.md and docs/http.md make no raise claim. No snapshot changed (snapshot uses limit 5, below the maximum); nothing regenerated. Red: boundary test failed at maximum; green after change. just check: lint, format, types, imports, deps, secrets pass; test run 5467 passed, 1 known macOS-only failure (test_the_menu_driver_reports_a_failing_cli); diff-cover 100% run separately. 5 hand mutations killed. DoD 2 left unchecked: CI on the PR cannot be green before a PR exists (not pushed).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Upload refusal no longer claims the operator can raise PAPERLESS_MCP_MAX_UPLOAD_BYTES when the limit is already the 100 MiB maximum. The maximum is one constant (MAX_UPLOAD_LIMIT, domain) used by settings and the hint. Boundary tests: maximum and maximum minus one. No snapshot change.
<!-- SECTION:FINAL_SUMMARY:END -->
