---
id: PLMCP-78
title: 'Complete the trash: look into, find, restore and empty'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:49Z'
updated_date: '2026-11-24T08:07:11Z'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approvals (2026-11-23, "Alles wie von dir empfohlen"): the trash stays opt-in; filters only show, acting takes shown ids in a second call; newer versions of a trashed document: say "restore first"; criterion 4 is changed: name the blocking id where Paperless lets it be known, otherwise say it is unknown; cap 1,000 entries with truncated; verify after emptying. Pending: OVERVIEW_GUIDE_LIMIT.

Design against today's code: /root/ exists as get_root (PLMCP-77) and is reused; list_trash and read_trash keep their shape with more row fields, the whole row is a port method of its own (look_up_in_trash), because the rows of the upload check stay small; the one request of up to 1,000 entries has the 64 MiB bound of decision-47 and is repeated with 100 entries when larger (truncated); after the write, entries that a partial read cannot decide are looked up one by one instead of by pages, which can skip rows on PostgreSQL; parts of a file use the SHA-256 of the whole stream (PLMCP-76).

Not test-first: the code of the first slice (e6edb36: filters, entry, file, outcomes) was written before its tests, so DoD 1 stays open. Its rules were broken by hand one after another afterwards (see the mutation note). The registry table of criterion 7 and the scenarios were red first; the answer texts of criterion 7 were tested and written together.

Changed on purpose: OVERVIEW_GUIDE_LIMIT 2800 -> 2835 (37 bytes of two tool names, measured 2835; owner's approval pending); surface snapshot (two tools, filters, outcomes, restore texts, note of workflows); MEDIA_TOOLS + get_trashed_file (decision-54); docs/tools.md, docs/api-coverage.md, README counts (93 tools). Breaking output: restored of restore_from_trash -> entries (release notes). No gate or threshold weakened otherwise.

Open: permissions of a trashed document is not read (design); the API would send it with full_perms=true, in place of user_can_change and is_shared_by_requester (seen in the source: PassUserMixin serves TrashView, too). The probes P1 to P7 were not run by hand; the contract and E2E tests cover P1, P2, P4, P5 and the owner change of P7. P3 (1,000 large rows) and P6 (emptying that fails) are on the fake only.
<!-- SECTION:NOTES:END -->
