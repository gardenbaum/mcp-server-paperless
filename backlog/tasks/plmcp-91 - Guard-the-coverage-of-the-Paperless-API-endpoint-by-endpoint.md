---
id: PLMCP-91
title: Guard the coverage of the Paperless API endpoint by endpoint
status: In Progress
assignee: []
created_date: '2026-11-23T12:24:30Z'
updated_date: '2026-11-23T17:23:23Z'
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
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Derivation: the schema guard records every request an adapter method sends in the adapter tests (port method, operation, query and body fields), also streamed ones; the code scan (tools -> services -> ports, AST) says which tool reaches a port method; RawReader never counts. 2. Checks in tests/quality/test_api_coverage.py (self-tested on synthetic violations): uncovered operation not listed, stale entry, unused field not listed, open task missing or Done/archived (read from the task files, no CLI), exclusion whose quoted approval and date are not in the repository file. 3. Hand-written entries in docs/api-coverage.toml; generated table docs/api-coverage.md (just docs-coverage). 4. Docs: README link, development.md.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Derivation: tests/adapter/schema_guard.py records Observation(port method, operation, query, body fields) per guarded or recorded request (OBSERVED); tests/quality/api_coverage.py scans tools/services/ports with ast; the quality test runs the adapter suite once more in-process (about 10 s) so it works alone. Owner approvals: searched backlog/decisions and backlog/docs; none quotes an owner approval to exclude an endpoint (decision-25's deny list is a design rule, not an approval), so there is no [[exclusion]] entry; every uncovered operation is open under a task. Assignment: workflows/triggers/actions -> PLMCP-87; saved_views and ui_settings -> 88; mail_accounts, mail_rules, processed_mail, oauth -> 81; tasks/{active,status_counts,summary,id} -> 89; documents/delete -> 80; everything else (config, profile, token, search, next_asn, selection_data, storage_paths/test, remote_version, preview, root, share_links, documents/{id}/email, PUT of the taxonomy/user/group/config/document endpoints) -> 83. Fields: documents list filters, taxonomy filters and fields, users, config -> 83; mail -> 81; saved views -> 88; tasks and history paging -> 89; bulk_edit/bulk_edit_objects/bulk_download selection -> 80; rotate/merge/edit_pdf/remove_password/reprocess options -> 79; ?version= (not in the schema, listed by hand) -> 75 (document text), 76 (download), 74 (thumb), 83 (metadata, preview); PATCH content -> 75. Limits: a field counts as used only when an adapter test sends it, so a field the adapter supports without its own test is listed too (for example PATCH workflows order); coverage by tool is by name lookup through services too (every tool resolving names touches the taxonomy lists), the table shows five tools per endpoint; module-level helpers of services are followed by name. DoD 2 stays unchecked: just check stops at the macOS-only failure test_the_menu_driver_reports_a_failing_cli (Input/output error); everything else passes, diff-cover has no lines to cover (test and docs files only). DoD 3 and 4 belong to the PR.

Review round (a36caa0): the field level was wrong both ways, so AC 3 is unchecked again. TRUE SCALE of the first version: it listed 159 body-field entries over 17 operations (not one example) that tools do send, 84 of them proven by the reviewer, because a field counted as used only if an ADAPTER test sent it; PATCH config alone had 41 of 42 false entries. It also credited fields to tools that never send them (any request of a reached port method counted), so dropping remote_ocr in tools/pdf.py or having run_saved_view not search stayed green. NEW DERIVATION: the tool tests run with their ports recorded (tests/observation.py: Recorder, ToolMarker; tests/harness.py) with the name of the running tool; each recorded call goes to the real adapter on a transport that answers with the fullest answer the adapter tests saw per operation; the request it builds is the evidence (tests/quality/derive.py, about 45 s inside the coverage test). The static scan of the code is gone. Fields are compared at every depth (a.b, a[].b) and booleans and enums by value (body:method=rotate, body:flag=true). tests/tools/test_arguments.py gives every tool a scenario with all its arguments and every enum value. The table says which fields only a saved view reaches (has_duplicates, owned by PLMCP-86 as a search filter) and the TOML lists fields the adapter sends by itself ([[internal]]: tasks owner, ordering, date_created_after; processed_mail ordering; the saved-account body of mail_accounts/test, PLMCP-81 AC 4). Exclusion approvals need a non-empty quote, an ISO date, a Markdown file under backlog/ and a paragraph that holds both and says the owner approved. REMAINING LIMIT (why AC 3 stays open): a field a tool supports that no tool test sends is still listed as open (the sweeps cover the tools' arguments, not every state the adapter compares: for example imap_security equal to the account's current value is not sent). Now 58 operations open and 676 field or value keys open (339 under PLMCP-83, 147 under 88, 65 under 87, 45 under 81, 32 under 89, 23 under 79, 21 under 80, 3 under 75, 1 under 76).

Re-review round 2 (9be6b0e). CURRENT DERIVATION FIRST: the coverage test derives everything from tests/tools/test_arguments.py: its scenarios run with the ports recorded and tagged with the running tool; every recorded call goes to the real adapter on a transport that answers with the fullest answer the adapter tests saw per operation; the request it builds is the evidence (tests/quality/derive.py, about 20 s). A scenario of a writing tool fails when it makes no write call (the services leave unchanged values out, so each value is sent from a state where it is a change), and a last test fails when a tool or an argument of its schema is in no scenario. Replayed calls must each send a request. Fields hang under their parent: only the parent is listed while it is unsent, its children and values appear once it is sent. Correction of the first note: the 159 body entries were ALL body-field entries over 33 operations of the first version; at least 84 of them, in 17 operations, were false. Now 58 operations open, 296 fields and 25 values open (fields by task: 83 195, 81 37, 87 16, 79 13, 88 12, 89 11, 80 8, 75 3, 76 1; values: 89 10, 80 9, 87 4, 83 2), every value with a reason in the TOML. Internal rule ([[internal]]): fields no tool argument can choose, namely fixed constants (ordering, fields, full_perms, owner, date_created_after) and the stored object sent back (mail_accounts/test and /process/ body, document_ids of a bundle rebuild); id__in and task_type are decided by arguments and are not internal. A value is never credited from the answer alone only by the tests: the page says an adapter may copy values from the stored object. AC 3 is checked again: the table now lists exactly what no tool test makes a tool send, with every value reasoned; the remaining limit (stated on the page) is that a supported field no scenario sends would show up as open, and the completeness test makes a missing scenario fail.
<!-- SECTION:NOTES:END -->
