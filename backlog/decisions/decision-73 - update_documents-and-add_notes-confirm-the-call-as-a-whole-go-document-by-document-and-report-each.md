---
updated_date: '2026-11-24T16:22:41Z'
id: decision-73
title: >-
  update_documents and add_notes confirm the call as a whole, go document by
  document and report each
date: '2026-11-24T16:22:18Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
update_documents (AC 1 of PLMCP-80) changes many documents with values of their own, add_notes (AC 4) adds notes to many. Paperless has no endpoint for either: each document needs a request of its own, so a call can end half way. A PATCH has no condition, replaces tags and custom fields as whole lists and mirrors document links on other documents. Repeating a POST of a note adds a second one.

The owner approved the design on 2026-11-24: "Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits." (draft 80: numbers 1 to 20 as you recommend, with the limits named there), with 6a, 7a, 8a, 9, 17, 18a and 19a for these two tools (PLMCP-80).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Everything that can be known is checked before the first write**, and a wrong item refuses the whole call, naming every wrong item (7a): unknown or hidden documents, no change right, names, values, empty edits, child tags that would go along, a document or a serial number named twice, a serial number another document of the call holds at that point, a link that would be sent (also one already stored) to a document that is hidden, gone or may not be changed, two items that contradict each other through a mirrored link, a serial number above 4294967295, an owner change by another than the owner (where the user may be read, and not for a superuser, as Paperless decides), and a link that would be sent to a document an earlier item gives away (one that item already mirrored is not sent). A refusal tells everything in full; one above 100,000 characters cuts the details, says how much is missing and names every wrong item.
- **One confirmation covers the call, whenever something changes** (19a, 8a), the owner changes of decision-12 included. It names every document, for the first 20 the old and new values and for the rest the fields, and, whatever their number, every owner change, every tag to be created and the other documents that mirrored links change (at most 100, as decision-71) (6a). Owner changes and new tags come first. Two spellings of one new tag are both named. Names and texts are quoted and cut as decision-44 and decision-64 say, except the names of new tags where the dialog says that it creates them: they are escaped as usual, but shown uncut (at most 128 characters, also where every one is escaped). The length of a dialog is not capped. For different notes it shows the start of the first 20, of the rest their length. All values, old and new, all note texts and all other documents are bound; the preview shows all values.
- **One request per document, in the order given, one at a time** (17). Right before it the document is read again. If a value the call touches is not the one that was checked, nothing is written (changed_meanwhile); links that earlier items of the call mirrored are expected. What became as asked in the meantime is not sent.
- **Each document ends in one status**, and later failures stop only that document. Nothing is started 40 seconds after the call began (decision-50's number; reading the documents and linked documents and creating tags count, and what is not checked by then refuses the call; the lists that names are looked up in and the request for the signed-in user are not bounded by it, only by their timeouts; a preview writes nothing and may take longer), after HTTP 429 or a rejected credential, or after two lost answers in a row. A write is never repeated; after a lost answer the document is read once: as asked is changed, anything else unknown, with the values that were seen. left names what is not as asked; repeating the call is safe, except that a document given away may be hidden from the user then and refuses the repeated call, so it has to be left out (the message, the description and the manual say so). also_changed names the other documents of what was sent. If nothing was seen changed and something failed, the call is an error.
- **Tags** that do not exist are created once, before the first document; if one cannot be, no document is changed and the created ones are named. Created tags stay, also when every document then ends as changed_meanwhile or failed.
- **Serial numbers** move when the document that releases them comes first; a swap takes two calls (18a).
- **Notes** whose text (without the whitespace around it, which Paperless drops) is on the document already are skipped (9). That needs the permission to view notes, without which the tool refuses. Notes are read in one list request and again per document right before its note is added. Paperless's own "could not save" can come after the note was saved, so it is looked up, too.
- **add_notes by filter or view** follows decision-69 and decision-71: never by itself, at most 100 notes a call of at most 1,000 matches, exclusions that are not there refuse. update_documents takes ids only.
- The answer of update_documents has at most 100,000 characters (the number of decision-70): above that values are cut to 200 characters, then left out from the end (truncated); if that is not enough, the titles of also_changed, the rest of a long message and the last ids of also_changed; every document of the call and its status stay. Names of created and parent tags are never cut, so more than about 700 of them could still exceed the bound.
- On 2026-11-24 the owner approved, as recommended by the main agent, with "Bitte alles wie von dir empfohlen." (please do everything as you recommend): the names of new tags uncut in the dialog; 100,000 characters as the bound of the update_documents answer and of a refusal; and for the longest of the tested worst cases 4897 instead of 3403 characters, with no cap on a dialog and owner changes and new tags first. The 4897 were measured while new tag names were cut; uncut the same case has 5388 characters, which the owner approved the same day with "Alles wie von dir empfohlen." (everything as you recommend).
- Supersedes, in decision-44 and decision-64, the cut to 60 and 240 characters for the names of new tags of update_documents, and, in the consequences of decision-64, 3403 as the length of the longest tested worst case: 5388 is approved. Everything else in both stands.
- Supplements decisions 6, 12, 69 and 71, and 44 and 64 otherwise.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Two requests per document: 100 documents can take more than 40 seconds; the rest is named and the same call goes on.
- Between the second read and the write another user can still change a document (Paperless has no conditional write). Nothing is atomic across documents.
- A client without dialogs calls a second time with confirm=true: what it was shown is not bound (the server keeps nothing between calls, as in decision-71); the call then checks against its own first read.
- A tag someone else added to a document in between keeps the call from changing its tags.
- A stored link to a trashed document refuses a change of the other custom fields of its document, as Paperless would.
- Paperless takes document links off the other side before it checks whether the owner may be changed. An item that does both is refused beforehand where the user may be read (view_uisettings); for any other user a refused one may have unlinked them, and says so.
- The answer to a PATCH comes from before the workflows for changed documents run: what they change is not in the result.
- After a lost answer a note with this text counts as added, also if someone else added it in that moment.
- The same text twice on one document is possible with add_note only.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_bulk_updates.py` and `tests/services/test_bulk_notes.py` (marker decision-73): every status, the limits, what a confirmation shows and binds, links and serial numbers inside a call; as properties: every document ends in exactly one status and `left` names the rest, no document ends with a note twice, no call by filter alone writes.
- `tests/domain/test_bulk_items.py`: the fields equal those of update_document. `tests/tools/test_bulk_items.py`: the same at the tools, and the dialogs on both protocol eras.
- `tests/contract/test_bulk_items.py` and `tests/e2e/test_bulk_items.py`: what Paperless 3.5.1 does with links, serial numbers and notes; a restricted user.
