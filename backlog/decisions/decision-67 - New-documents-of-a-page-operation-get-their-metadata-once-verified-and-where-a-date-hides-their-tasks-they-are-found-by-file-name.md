---
id: decision-67
title: >-
  New documents of a page operation get their metadata once verified, and where
  a date hides their tasks they are found by file name
date: '2026-11-24T13:13:05Z'
status: accepted
---
## Context

Splitting a document is of little use while every part has the title, type and date of the whole, and taking pages out of a document takes two operations whose order matters (PLMCP-79, AC 2 and 6). Both were put to the owner with the design of PLMCP-79; on 2026-11-24 the owner approved all 14 decisions: "Bitte alles wie von dir empfohlen umsetzen." (please do everything as you recommend).

Paperless 3.5.1 cannot record the task of a file whose copied metadata holds a date among the custom fields: the work is done, but no task is listed (decision-61). Such parts could be neither checked nor given metadata, and an original never replaced. Asked about that, the owner ruled on 2026-11-24: "das neue Dokument über den Dateinamen finden und prüfen" (find and verify the new document by its file name).

## Decision

- **Metadata per part.** `split_document` takes `part_metadata`, one entry per part, and `extract_pages` takes `metadata`: title, document type, correspondent, date, and the whole list of tags. A field left out keeps what Paperless copied. Names are resolved and titles checked before anything starts; what they mean is shown in plan and confirmation and bound by it.
- It is set through the document edit (`DocumentEditor`), one part after the other, and only on a part that is verified as decision-60 says: known to be this call's, waited for, succeeded, exactly one document of its own, readable, with its pages. Not later than 45 seconds after the start. One failure does not stop the others. An answer with other values than asked for counts as not set; the tags must be exactly those asked for and their parents. The order of pages belongs to what is confirmed.
- `parts` reports every part (state, document, metadata, message) and in `next` the calls that finish it; `original` says what became of the original. Nothing is remembered between calls.
- **Nothing is trashed, and no page removed, while any requested metadata is not set** (owner decision 4a), besides every condition of decision-60.
- **extract_pages** makes the new document, verifies it, sets its metadata, reads the source again, and only then, with `remove`, adds a new version of the source without the pages, not later than 45 seconds after the start. `remove` needs confirm, takes the newest version only, and never every page. After Paperless accepted the request nothing is raised, unexpected errors included (the log names their type alone): the result says whether the pages stayed, went, or that this is not known, and names the leftover. The document must show the produced version with the remaining pages to count as shortened.
- **Found by file name.** Where no task is listed for a request that makes new documents, a date among the copied custom fields explains it, and the call waits, the new documents are looked for by the names Paperless gives its files (`{id}_edit_{n}.pdf`, `{ids}_merged.pdf`, `{id}_unprotected.pdf`), read from `original_file_name`. They stand in the result like those of tasks, without a task id, and everything of decision-60 applies.
- They are taken only if nothing else can be meant; otherwise no document is named, patched or replaced for, and the result says why. The conditions: the user is known and owns them; they were added since the queued task started; there is exactly one of each name and none too many; no listed task makes a file of these names; each holds the date that was copied; no other editing, merging, splitting, unlocking or page deleting of this user ran at the same time or has started since; and of such operations that ended in the last hour before, each is known to have made something else (the first file of another document, or a new version), because the files of an earlier one of the same document could still be waiting for their consumption and Paperless does not say which document an operation worked on. Operations are the caller's if the caller owns them: 3.5.1 records `request.user.pk` as owner of every queued operation (all its views require a logged-in user, however it logged in), the operation and the consumer load that user by id, and only deleting the user empties the owner. So an operation without an owner is of a user who was deleted since, whose documents are not the caller's unless somebody gives them to the caller, or an update Paperless queued for itself. The owner approved this on 2026-11-24 ("alles wie empfohlen", all as recommended). That last condition is looked into once every file is there, or at the end, and waited for within the bound; the owner kept the hour on 2026-11-24 ("Bitte wie von dir empfohlen.", please as you recommend), and likewise that `tags` is the whole list and that rotating stays out.
- Supplements decision-60 and decision-61: "doubted" there is the same here; a task the user may not see is still not followed.

## Consequences

- A call with metadata, removal or replacement waits up to 30 seconds by default. Where OCR takes longer, the parts stay as they are and `next` names the calls.
- A part gets the inbox tags when it is consumed; `tags` is the whole list, so it takes them away unless they are named.
- The change of a part is an update: workflows for changed documents run, and can change it again afterwards.
- Splitting the same dated document twice within an hour, a removal of a password that had none, or an earlier operation whose new documents were deleted meanwhile leaves the later request in doubt: the documents are made, nothing is done to them. So does a user without `view_uisettings`.
- An operation of more than an hour before whose files are consumed only now would not be noticed, and could stand in for a more recent one when they are counted. An upload of the same user whose own task is hidden too, with such a name, such a date and the right pages, added in that time in place of a part that does not exist yet, would be taken for it.
- An operation of a user who was deleted is not looked at any more; until then its row without an owner was a doubt. So if an administrator hands that user's documents over to the caller within the hour, or a workflow makes the caller their owner, one of the same name, date and pages could be taken. For users who still exist that was never covered.
- Rotating is not covered: it adds versions and replaces nothing. A date still hides its tasks.
- The guide of the overview grows by the 16 bytes of one name, to 2865 of the 2880 the owner approved for the three tools of PLMCP-79.

## Alternatives

- Copying no metadata and setting all of it here: the task would be listed, but grants and the owner's other settings would need more rights and a half state (not the owner's choice).
- A window shorter than an hour, or none: an earlier request of the same document that waits for OCR would be taken for the later one.
- Aborting at the first failed part, or undoing what was made: the new documents are the only copy of work that succeeded.

## Enforcement

- `tests/services/test_pdf_parts.py` and `tests/services/test_pdf_replacing.py`: every row of the failure tables with the state afterwards, what is trashed and set, the search by name.
- `tests/adapter/test_pdf_copies.py`: the conditions. `tests/contract/test_pdf.py`: the search on the fake and on Paperless 3.5.1.
- `tests/services/test_confirmation_scope.py`: what a confirmation binds. `tests/tools/test_pdf.py`: the tools, and that the calls named in `next` are accepted. `tests/e2e/test_pdf.py`: on Paperless 3.5.1.
