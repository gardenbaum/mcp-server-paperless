---
id: PLMCP-78
title: 'Complete the trash: look into, find, restore and empty'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:49Z'
updated_date: '2026-11-24T07:46:37Z'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 67000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The toolset trash (off by default) already lists, restores (up to 100 ids, no confirm) and empties; the owner's agent did not see it. A list row has only id, title, deletion time and, for a version, its document. The API has only page and page_size for the list, and answers restore and empty all-or-none. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every field the API gives for a trashed document except content is readable (content: next criterion), and the list can be narrowed by title, document type, kind (document or version), deletion date and id over all pages up to a stated cap, with truncated when the cap is hit (decision-4)
- [ ] #2 Where Paperless serves them, text, thumbnail and file of a trashed document are readable; where not, the answer says so and names the closest way (restore first)
- [ ] #3 Restore and delete for good also accept a narrowed list, which is resolved to ids and shown first, and only those ids are acted on; deleting needs confirm, restoring does not (or a decision changes that)
- [ ] #4 The result lists each entry; that Paperless restores or deletes all or none is stated, and an id that blocks the call is named
- [ ] #5 Whether reading and restoring belong in the default profile is decided; if a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [ ] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each filter, the cap, trashed content, restore and delete by narrowed list, all-or-none refusals and a restricted user
- [ ] #7 Every tool that trashes something (documents, versions, pdf, bulk, resolving duplicates) says how to restore it and which toolset that needs, as delete_document does
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
1. Rows, TrashFilter, scan (one bounded request, decision-47) and list_trash filters, tests first
2. get_trash_entry (all fields, text slice) and get_trashed_file (thumbnail, file, parts as decision-45)
3. restore/empty by filter (shows, never acts without a dialog bound to the ids), per-entry outcomes, reading the trash again, naming the blocking id
4. Criterion 7 texts and registry table
5. Fake, contract, tool and E2E tests; decisions 54/55; docs, snapshots
<!-- SECTION:PLAN:END -->
