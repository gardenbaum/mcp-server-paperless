---
id: PLMCP-47
title: Model Paperless 3.5 behaviour in FakePaperless and refresh the vendored docs
status: To Do
assignee: []
created_date: '2026-11-22T12:40:00Z'
labels: []
dependencies: []
priority: medium
type: chore
ordinal: 47000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
PLMCP-46 made the adapter handle paperless-ngx 3.5.1 and made the live tests version-aware, but FakePaperless still models only the pinned 3.4.2 (config readable by anyone, blank notes accepted, synchronous document operations). Proposal, needing a decision because it moves the pinned version: pin 3.5.x (justfile PAPERLESS_VERSION, tests/e2e/compose.yaml digest, just docs-sync-paperless to refresh docs/vendor/paperless and openapi.json, which also adds task_id to the document-operation results, the note format field and the manual workflow trigger type), then drop the 3.4 branches or keep both behind the fake's release. Also: the tag-deletion preview text and the delete_taxonomy_item description say child tags of other users are deleted with the tag, which 3.5 refuses (403); reword to cover both and give that refusal a hint.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A decision records the new pin (or keeps 3.4.2) and why
- [ ] #2 FakePaperless can act as 3.4.2 and 3.5.x for the config read, note validation and mail password mask, with contract tests for both
- [ ] #3 The tag deletion preview and tool description are true on 3.4.2 and 3.5; the refusal on 3.5 carries a hint; snapshot changes are stated
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
