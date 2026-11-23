---
id: PLMCP-91
title: Guard the coverage of the Paperless API endpoint by endpoint
status: Done
assignee: []
created_date: '2026-11-23T12:24:30Z'
updated_date: '2026-11-23T20:43:00Z'
labels: []
dependencies: []
priority: high
type: chore
ordinal: 61500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Nothing shows endpoint by endpoint what the server covers, through which tool, and why not. A guard built first shows every following task what is still missing. Split from PLMCP-83; the owner put it first on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A generated table lists every path and method of the vendored schema with the covering tool, the open task (PLMCP-83 if no other task has it), or an owner-approved exclusion quoted with its date; paperless_api_get (raw) alone does not count as covering
- [x] #2 A test fails when an endpoint is neither covered nor listed, also after a schema refresh; it derives coverage from the code or the contract tests, not from a hand-written claim
- [x] #3 Request body fields and query parameters of covered endpoints that no tool uses are listed from the schema with their open task or an owner-approved exclusion, under the same test; query parameters missing from the schema, such as ?version=, are listed by hand
- [x] #4 Every open entry names a Backlog task, and a test fails when that task does not exist or is Done while the entry is still open
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Derivation: the schema guard records every request an adapter method sends in the adapter tests (port method, operation, query and body fields), also streamed ones; the code scan (tools -> services -> ports, AST) says which tool reaches a port method; RawReader never counts. 2. Checks in tests/quality/test_api_coverage.py (self-tested on synthetic violations): uncovered operation not listed, stale entry, unused field not listed, open task missing or Done/archived (read from the task files, no CLI), exclusion whose quoted approval and date are not in the repository file. 3. Hand-written entries in docs/api-coverage.toml; generated table docs/api-coverage.md (just docs-coverage). 4. Docs: README link, development.md.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
How the coverage is derived (present state). The coverage test (tests/quality/test_api_coverage.py) runs the scenarios of tests/tools/test_arguments.py inside the process with the ports recorded and tagged with the running tool. Each recorded call goes to the real adapter on a transport that answers with the fullest answer the adapter tests saw per operation; the request it builds is the evidence (tests/quality/derive.py, about 20 s). Every replayed call must send a request. paperless_api_get never counts. Fields are compared at every depth (a.b, a[].b); booleans and enums by value. A field under an unsent parent is listed only as the parent; its children and values appear once the parent is sent. Each scenario of a tool that is not read-only must make a write call (any *_writes port; per call, not per argument), and each value is sent from a state where it is a change, because the services leave unchanged values out. The coverage test fails when a tool or an argument of its schema is in no scenario, so test_arguments.py passes alone, in any order and one test at a time. Hand-written file: docs/api-coverage.toml. [[open]] and [[open_fields]] name a task that must exist and not be Done or archived (read from the task files, no CLI needed); every listed value has a reason. [[exclusion]] needs a non-empty quote, an ISO date and a Markdown file under backlog/ with a paragraph that holds both and says the owner approved; no such approval exists in the repository, so nothing is excluded. [[internal]]: fields the adapter chooses itself that a task wants as a tool argument; they stay open under that task: tasks owner, ordering, date_created_after (89); processed_mail ordering and the body of mail_accounts/test (81); the documents fields projection (80, AC 3); document_ids of a bundle rebuild (83). [[plumbing]]: fields the adapter chooses itself that no task is known to want (full_perms, the fields projection of PATCH documents and GET documents/{id}, page and page_size of the workflows list helper, the stored account sent back by process); shown apart on the page, no work, applied where it was noticed and not a census of every constant. Plumbing and internal rows are checked: the operation is in the schema and covered, plumbing fields are in the schema and sent by some tool, one row per operation, no field in both. Counts: 58 operations open (83: 31, 87: 14, 81: 5, 89: 4, 88: 3, 80: 1); 276 fields open (83: 182, 81: 29, 87: 16, 79: 13, 88: 12, 89: 11, 80: 9, 75: 3, 76: 1) and 25 values of sent fields (89: 10, 80: 9, 87: 4, 83: 2), each value with its reason; 13 plumbing operations. Owners: mail owner and set_permissions to 81; bulk_download selection to 80; POST /api/ui_settings/ under 88 for the saved-view part, the rest of ui_settings is PLMCP-83 (noted on both tasks); has_duplicates is only sent through run_saved_view (PLMCP-86 wants it as a search filter; the page shows it in the saved-view section). Limits (also on the page): a field a tool supports that no scenario sends is listed as open; the adapter may copy values from the stored object (the canned answer), which then count; a covered GET does not mean its whole answer is shown (ui_settings view visibility, 88 AC 2); an unsent or excluded parent hides children a schema refresh adds; requests of streamed bodies are recorded but not checked against the schema. DoD 2 stays unchecked: just check stops at the macOS-only failure test_the_menu_driver_reports_a_failing_cli.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Merged in #101. docs/api-coverage.md is generated from what the tools really send; a test fails for any endpoint, field or value that is neither covered nor open under a task. No product code changed. Open then: 58 of 163 operations, 276 fields, 25 values; no exclusions.
<!-- SECTION:FINAL_SUMMARY:END -->
