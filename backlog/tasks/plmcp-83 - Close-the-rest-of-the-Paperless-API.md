---
id: PLMCP-83
title: Close the rest of the Paperless API
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:53Z'
updated_date: '2026-11-25T14:42:51Z'
labels: []
dependencies:
  - PLMCP-91
  - PLMCP-74
  - PLMCP-75
  - PLMCP-76
  - PLMCP-77
  - PLMCP-78
  - PLMCP-79
  - PLMCP-80
  - PLMCP-81
  - PLMCP-82
  - PLMCP-85
  - PLMCP-86
  - PLMCP-87
  - PLMCP-88
  - PLMCP-89
  - PLMCP-90
priority: medium
type: chore
ordinal: 71000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The guard (PLMCP-91) shows endpoint by endpoint what is covered. After the other tasks of this feedback, this one closes what is left. The owner wants reading and writing, as complete as the API allows. Requested by the owner on 2026-11-23. Paperless 3.5.1 (PLMCP-97) added fields that no other task names: the `format` of a note (plain or markdown) when one is added and in the notes read back, and `from_webui` of the delete, reprocess and merge_as_versions requests (the web UI sets it to keep its selection; the bulk_edit request has it already). They stay open here until a tool sends them or the owner approves an exclusion.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each endpoint and field the table of PLMCP-91 still leaves open gets a tool, or an exclusion the owner approved, quoted with its date in the table; when this was written that included next ASN, autocomplete, storage path test, selection data and profile (which returns the API token and is denied by decision-25)
- [ ] #2 The README's statement about coverage matches the table
- [ ] #3 New operations have contract tests on the fake and Paperless 3.5.1, tool tests and E2E
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-91 (coverage table): POST /api/ui_settings/ is listed open under PLMCP-88 for the saved-view part; the part of /api/ui_settings/ beyond saved views (the other settings of the user) belongs to this task.

From PLMCP-74: query:version of GET /api/documents/{id}/metadata/ is covered now (get_document_pages) and left the coverage table; query:version of thumb and the preview endpoint stay open here. The core toolset has 20 of 20 tools since decision-42.
<!-- SECTION:NOTES:END -->
