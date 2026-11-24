---
id: PLMCP-80.3
title: 'Bulk changes c: update_documents and add_notes'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T12:54:34Z'
updated_date: '2026-11-24T17:39:35Z'
labels:
  - bulk
dependencies:
  - PLMCP-80.1
parent_task_id: PLMCP-80
priority: medium
type: feature
ordinal: 94000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-80: different values need one call per document. Design approved by the owner on 2026-11-24 (decisions 1 to 20 as recommended); file design-plmcp-80.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 update_documents changes up to 100 documents with individual values in one call, every field update_document knows, with preview, confirmation and a result per document
- [ ] #2 Everything that can be known beforehand is checked before the first write; a failure names what was and was not changed
- [ ] #3 add_notes adds notes to many documents with a result per document and skips a note whose text is already there
- [ ] #4 Contract, tool and E2E tests cover limits, rate, partial failures and a restricted user
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
Stacked on PR #137 (80.2), which is merged first.
1. domain/bulk_items.py: DocumentValues (exactly the fields of update_document), DocumentNote, reports.
2. document_edits: EditPlan (names resolved, patch per state) shared by update_document and update_documents; behaviour of update_document unchanged.
3. services/document_runner.py: one after the other, 40 s, 429/401 stop, two lost answers in a row stop.
4. services/bulk_updates.py: phase 1 (two kinds of list request, every wrong item named), confirmation, tags once, re-read, compare, PATCH, re-read after a lost answer.
5. services/bulk_notes.py: selection or individual notes, notes read first and again before each POST.
6. tools, server, annotations, docs, coverage, snapshot, guide limit, decision; contract, tool and E2E tests. Each test red first.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (draft PR #138, stacked on #137): update_documents and add_notes in the toolset bulk, services/document_runner.py (one by one, 40 s, 429/401 and two lost answers stop), services/bulk_updates.py, services/bulk_notes.py, EditPlan shared with update_document, decision-73.

Owner approvals (2026-11-24): "Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits." (6a, 7a, 8a, 9, 14, 17, 18a, 19a). OVERVIEW_GUIDE_LIMIT 2897 to 2928 (31 bytes, approved up to 2943).

Evidence: domain, contract (fake), service and tool tests, each red first (the dialog and tool tests were run red with the tools taken out); 100 % branch coverage of the three new service modules by their own tests; 24 mutants by hand, 6 survivors got tests, all killed. Text: update_documents 7.5 KB, add_notes 7.6 KB of definitions, bulk 50.4 KB.

Deviations from the design:
- Linked documents are read in further list requests of 100 each (the design says two list requests in all). Reason: the whole link list that is sent again is checked, also stored links, because Paperless refuses all of it if one target is gone or may not be changed, and mirrors again what was one-sided; both are named or refused beforehand.
- A serial number another document of the call still holds at that point is refused beforehand (known beforehand), not only a repeated one.
- Names of new tags are cut to 60 characters like every text the server did not write (decision-44); all are listed and bound. The design said in full.
- Answers of update_documents above 100,000 characters cut values to 200 characters, then leave values out from the end (truncated); every document and status stay. Not in the design.
- Notes are read again per document before each POST (the design reads them once), and Paperless's own 'could not save' is looked up, since it can come after the note was saved [read in the 3.5.1 source]. add_notes with notes takes several different notes for one document.
- An own new note after a lost answer is told by its text, not by its author (another request is needed for who the user is).
- The answer of a PATCH comes from before the workflows for changed documents run [read in the source], so workflow changes are not in the result.
- A lost change that is seen made in part shows what was seen.

Review round (head before: 7bb3b35).
OPEN, owner: (1) the hostile update_documents case of tests/tools/test_confirmation_text.py. Commit 7e808fe had cut its input (title, correspondent, select field, second document) to stay below WORST = 3403 and did not say so; that was a weakened test. The full case of de3f2e9 is back. Measured now: 4897 characters. WORST is not raised, the input not cut, so test_names_made_of_escapes_only_do_not_make_a_refusal_endless[update_documents] is RED. (2) Names of new tags in a dialog: cut to 60 (decision-44) or uncut (6a 'in full'); unchanged until answered. (3) A cap on dialog length: none yet. Measured dialog sizes: one document with 598 links 11,207 characters; 100 documents with 128-character titles and a new title each 10,299, with an owner change each 22,150; with titles made of escapes only 30,579 and 59,330.
Done: owner changes and new tags stand before the change list; a preflight refusal repeats at most 300 characters of a message and 600 per item and names every item; largest serial number, owner change by another than the owner and a link to a document an earlier item gives away are refused beforehand (get_signed_in, new port method, needs view_uisettings; without it the owner cannot be checked and a refused item that also drops links may have unlinked them, told in its why); also_changed only for what was sent; a partly seen change is a result; notes of one document count one document; two spellings of a new tag are named; NEXT_CALL has a place for the word; 40 s also bound tag creation and the list requests (what is not checked by then refuses the call, nothing is written; a preview may take longer, because bounding it would cut it; a running request ends with the client's timeout).
update_document itself does not check the largest serial number (schema ge=0 only): reported, not changed.
Found by the hosted run of 7bb3b35: live contract tests passed; the test of 100 documents failed (update_documents 100 in 12.1 s; add_notes stopped after two answers that were lost but looked up as added). A looked-up success no longer counts as silence (decision-73); why the answer of the POST is unusable for documents without a file is not known, the test now prints it. Its cleanup left rows that referred to the documents; fixed.
Deviations not listed before: different notes show the start of the first 20 only (of the rest their length; all bound); tag names cut to 60; truncation of answers above 100,000 characters (values to 200, then left out, then the message); an added note after a lost answer is told by its text, not its author; add_notes shows the first 80 characters of a single note and its length.
24 and 11 mutants by hand, survivors got tests.

Second review round (from 49a3ca9; the hosted full E2E of 49a3ca9, 45443967598, was green, with the silence rule that is now taken back).
A: what an earlier item mirrors is simulated, so two documents given away that link each other are changed; a list that would be sent again after giving away is refused, and where no order helps an earlier call is named. B: my cuts at 300/600 characters shortened ordinary refusals and are gone; a refusal is told in full up to 100,000 characters. OPEN, owner: that bound, together with the 100,000 of the answer. C: two lost answers in a row stop again, also if looked up (design 3.3), in both services. Cause of the failed 100-document test: the bare rows had no filename, so Paperless's handler returns before refresh_from_db and the POST answers the notes prefetched before [read in the source]. The rows get the name Paperless itself would generate, so the handler refreshes and moves no file; that the generated name equals the stored one and nothing is moved is read, not run. D: an answer without a note of the sent text is unusable (add_note too); an adapter test that pinned the opposite is replaced. Paperless only strips the text (DRF CharField) [read in the source; contract test]. E, G: affected ids named. F: decision-73 says what the 40 s do not bound. An owner change that ended unknown is named as perhaps given away, in the error too. Mutants at LINK_NEEDS and six more: killed.
Correction of the dialog sizes: 598 links with 120-character titles give 47,769 characters (11,207 was with short titles).
<!-- SECTION:NOTES:END -->
