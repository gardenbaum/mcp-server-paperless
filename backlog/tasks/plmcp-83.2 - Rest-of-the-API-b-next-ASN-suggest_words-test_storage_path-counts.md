---
id: PLMCP-83.2
title: 'Rest of the API b: next ASN, suggest_words, test_storage_path, counts'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T14:42:17Z'
updated_date: '2026-11-25T16:19:53Z'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probes P1 to P4 on 3.5.1 (temporary test). 2. Tests first per level: adapter, contract, service, tool, E2E. 3. Next ASN in InstanceReader.get_overview; DocumentReader.count_selection and suggest_words; TaxonomyReader.get_storage_path_template and render_storage_path; services/lookups.py; counts in ReadDocuments. 4. Decisions for counts and for the next ASN; guide limit +36, definition limit at most 1010; docs, snapshots.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first at adapter (10), contract (14), service (import errors), tool (12), then green; the security fix red first too (14). Probes P1 to P8 on 3.5.1: run 45638730209, job 139291151398 (temporary test, deleted). 49 hand mutants: 4 survived, killed by new tests and re-run. decision-100, decision-101. Deviation: field next_asn instead of next_archive_serial_number (definition limit). Security review: a template shows names of hidden objects, so a document that refers to any is refused; suggest_words mirrors Paperless (words of hidden tag names of visible documents) and says so: the owner may accept it.

Owner, 2026-11-26, asked whether the disclosure of suggest_words (words of hidden correspondent, type and tag names of visible documents, the same words Paperless autocomplete gives) is accepted with the note in the tool description and decision-100: "Alles wie von dir empfohlen." (everything as recommended by you; the recommendation was to accept).

Independent review, closed test first: M1 the rendered path was read unbounded (now PaperlessClient.post_json, 64 KiB, uncompressed, stopped while it arrives; proven with a mock transport that never ends). L2 no row is read once the counts took the 40 s (left, notice). L6 the schema guard exempts only the empty body. L7 a path that is only a dot is a 500 in 3.5.1, told in a note, the fake too. decision-100: size, that Paperless still renders, soft-deleted custom field values.
<!-- SECTION:NOTES:END -->
