---
id: PLMCP-78
title: 'Complete the trash: look into, find, restore and empty'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:49Z'
updated_date: '2026-11-24T10:25:35Z'
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
- [x] #1 Every field the API gives for a trashed document except content is readable (content: next criterion), and the list can be narrowed by title, document type, kind (document or version), deletion date and id over all pages up to a stated cap, with truncated when the cap is hit (decision-4)
- [x] #2 Where Paperless serves them, text, thumbnail and file of a trashed document are readable; where not, the answer says so and names the closest way (restore first)
- [x] #3 Restore and delete for good also accept a narrowed list, which is resolved to ids and shown first, and only those ids are acted on; deleting needs confirm, restoring does not (or a decision changes that)
- [x] #4 The result lists each entry; that Paperless restores or deletes all or none is stated, and an id that blocks the call is named
- [x] #5 Whether reading and restoring belong in the default profile is decided; if a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [x] #6 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover each filter, the cap, trashed content, restore and delete by narrowed list, all-or-none refusals and a restricted user
- [x] #7 Every tool that trashes something (documents, versions, pdf, bulk, resolving duplicates) says how to restore it and which toolset that needs, as delete_document does
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rows, TrashFilter, scan (one bounded request, decision-47) and list_trash filters, tests first
2. get_trash_entry (all fields, text slice) and get_trashed_file (thumbnail, file, parts as decision-45)
3. restore/empty by filter (shows, never acts without a dialog bound to the ids), per-entry outcomes, reading the trash again, naming the blocking id
4. Criterion 7 texts and registry table
5. Fake, contract, tool and E2E tests; decisions 54/55; docs, snapshots
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approvals (2026-11-23, "Alles wie von dir empfohlen"): the trash stays opt-in; filters only show, acting takes shown ids in a second call; newer versions of a trashed document: say "restore first"; criterion 4 is changed: name the blocking id where Paperless lets it be known, otherwise say it is unknown; cap 1,000 entries with truncated; verify after emptying. On 2026-11-24 ("wie von dir empfohlen umsetzen"): OVERVIEW_GUIDE_LIMIT 2800 -> 2835 with the full toolset summaries.

Design against today's code: /root/ exists as get_root (PLMCP-77) and is reused; list_trash and read_trash keep their shape with more row fields, the whole row is a port method of its own (look_up_in_trash), because the rows of the upload check stay small; the one request of up to 1,000 entries has the 64 MiB bound of decision-47 and is repeated with 100 entries when larger (truncated); after the write, entries that a partial read cannot decide are looked up one by one instead of by pages, which can skip rows on PostgreSQL; parts of a file use the SHA-256 of the whole stream (PLMCP-76).

Not test-first: the code of the first slice (e6edb36: filters, entry, file, outcomes) was written before its tests, so DoD 1 stays open. Its rules were broken by hand one after another afterwards (see the mutation note). The registry table of criterion 7 and the scenarios were red first; the answer texts of criterion 7 were tested and written together.

Changed on purpose: OVERVIEW_GUIDE_LIMIT 2800 -> 2835 (37 bytes of two tool names, measured 2835; approved by the owner on 2026-11-24); surface snapshot (two tools, filters, outcomes, restore texts, note of workflows); MEDIA_TOOLS + get_trashed_file (decision-54); docs/tools.md, docs/api-coverage.md, README counts (93 tools). Breaking output: restored of restore_from_trash -> entries (release notes). No gate or threshold weakened otherwise.

Permissions (owner, 2026-11-24, "Bitte alles wie von dir empfohlen umsetzen"): get_trash_entry reads them with a second bounded request (full_perms); a failure names the gap. The probes P1 to P7 were not run by hand; the contract and E2E tests cover P1, P2, P4, P5 and the owner change of P7. P3 (1,000 large rows) and P6 (emptying that fails) are on the fake only.

Mutation by hand (74 single changes to the services, adapter, filter model, hint and answer texts, each against the targeted tests in a copy): 6 survivors got a test (a repeated row, a full last page, at most 100 single lookups, max_bytes over the limit, an unknown filter field, the original upload of a document without listed versions). One remains and is equivalent: the scope of an accepted filter, whose text already names every id it acts on.

Paging ends whatever Paperless answers: reading on for an id (and for an entry) followed next without a check, as the old code did; it now stops when the answer does not get on, at an empty page (list) and after 1,000 pages of 100 (tests: the same page again, empty pages, always one more). Found because one hand mutant of a paging condition looped in a test; no further mutant of that kind was run, the hosted mutation run covers them.
Full local run (375cef2): green but for the two known macOS failures and a wrapped line in the description of delete_document, fixed since; the fix and the paging bound were run with targeted tests only.

Review of 16f55da (2 Important, 9 Minor), all fixed: UTC days; fall back to small requests on transient failures; what pages may have skipped is not said for sure; the failure beside a success is told; outcomes are looked for; versions with the title and type of their document; only the wanted row is kept; the cost per file part is in decision-54. Security finding: no read of whole rows is unbounded; a compressed answer is refused unread. The schema guard lists full_perms for GET /api/trash/. SCAN_TIMEOUT 120 -> 60 s, because there is a fallback now. PLMCP-99: the filter path and the second call are tested with the dialog required. The page fallback for ids still uses list_trash, which has no size limit on main.

Delta review of 07903bf (1 Important, 5 Minor), fixed: permissions and user_can_change of a deleted version are its document's (null with a named gap if unreadable, is_shared_by_requester null); list_trash without a title or type filter works while titles cannot be looked up; "deleted" only with a 404 of /root/; no second large read in a call, none after 429; texts. The single lookups after a change are ten at a time within 20 seconds instead of at most 100, so that all=true of more than 100 entries is still verified.

Last review of 8b39d13, fixed test-first (9 red): document_exists tells 404 from 403 (DocumentReader; get_root unchanged), every answered lookup of a group is kept, and the naming of grants is inside the named gap.

Merged with #122 (b73f310). Five review rounds, the last ones clean; Codex without open findings; hosted full E2E 45367465274 green on 91dc609, after which only two wordings changed. DoD 1 stays open (first slice written before its tests), DoD 2 unchecked (macOS-only failures). Follow-ups: PLMCP-111, and get_document_thumbnail shows trashed documents to view grants (design: own task, PLMCP-102).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The trash can be filtered, looked into (get_trash_entry with permissions, get_trashed_file) and acted on by a narrowed list that is shown first; every entry reports its outcome as seen afterwards (decisions 54 and 55). restore_from_trash answers entries instead of restored (breaking). OVERVIEW_GUIDE_LIMIT 2800 to 2835, approved by the owner.
<!-- SECTION:FINAL_SUMMARY:END -->
