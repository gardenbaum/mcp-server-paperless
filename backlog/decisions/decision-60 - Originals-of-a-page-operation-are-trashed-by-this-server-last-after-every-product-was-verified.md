---
updated_date: '2026-11-24T09:17:35Z'
id: decision-60
title: >-
  Originals of a page operation are trashed by this server, last, after every
  product was verified
date: '2026-11-24T09:16:24Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Paperless can trash the original after `edit_pdf` and `remove_password` (`delete_original`) and after `merge` (`delete_originals`). It does so once the tasks that consume the new files succeed. A task also succeeds when the consumer stops without making a document (a barcode split, a workflow), and a merge leaves out what it cannot read without an error. So an original could go although a part of it exists nowhere else. With several outputs it also clears the archive serial number of the original, and nobody gets it.

Who trashes the original was put to the owner with the recommendation that this server does, last, for split_document, delete_pages, edit_pdf, merge_documents and remove_document_password. On 2026-11-24 the owner approved it: "Bitte alles wie von dir empfohlen umsetzen." (please do everything as you recommend) (PLMCP-79, decision 3a).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `replace_original` and `delete_originals` are composed here: the files are made first, as new documents; then this server waits (30 seconds unless `wait_seconds` says otherwise), verifies, and only then moves the originals to the trash. Paperless is never asked to delete (`delete_original`, `delete_originals` are not sent).
- **Before anything starts** the right to delete is checked where it can be known (`/api/ui_settings/` lists the permissions of a user who may view it, and the record tells the owner): a user without it is refused, also with `dry_run`. What cannot be known does not stop the call.
- **Verified** is: every expected task is known to be this call's own and succeeded, each names exactly one document of its own, the document can be read, and its page count is the expected one. Parts are told by the number in their file name, not by their order. Paperless does not name the request a file belongs to, so tasks found by name are doubted, and nothing is trashed for them, unless they were created while the queued task of this call ran, there are exactly as many files of these names as asked for, copies carry the consumer flag of a copy, the user is known, and no other editing, merging, unlocking, splitting or page deleting of this user ran at the same time. A merge also has to name every document in its file name, or have the sum of the pages; if neither can be told it is not verified.
- Directly before its own deletion, each original is read again and the clock is checked: its newest version and archive serial number must be the ones that were accepted, and nothing is trashed later than 45 seconds after the operation was started. The rest stays and is named. A deletion that answers with an error is looked up: gone, still there, or not known, and the result says which.
- **Archive serial numbers.** Where exactly one new document replaces one (delete_pages, edit_pdf with one output, merge: the metadata document's number, else the first), the number moves before the original is trashed. If it cannot move, or its own original then is not trashed, it goes back to the original; if that fails, the result says on which document it is, that it is on neither, or that this is not known. Moving it changes both documents, so workflows for changed documents run; plan and confirmation say so and name the number, and any number that is blocked. With several outputs, and for remove_document_password, the number stays with the trashed original. A new document that has a number already keeps it.
- Otherwise the originals stay; `message` says why, `trashed_ids` is what went, and `next` names the calls that finish the work, with their conditions: a deletion is suggested only for what was verified, or after the tasks succeeded and made exactly the expected documents with the expected pages, and never when a product is missing, hidden or in doubt. Nothing is remembered between calls.
- An older version, or first files, cannot replace a document with newer versions: the new document would not hold them.
- Supplements decision-6, decision-15 and decision-44; the confirmation still comes before the first request.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Where OCR takes longer than the wait, the original stays and one more call (`delete_document`) is needed; before, Paperless did it by itself later.
- A user whose rights cannot be read (no `view_uisettings`) and who may not delete is told so after the new documents were made: the original stays and they are named.
- An upload of the same user, named like a part, that arrives while the queued task runs and carries the flag of a copy, in place of a file whose task is hidden, would be taken for it; its page count would still have to fit.
- Another operation of the same user at the same time keeps the originals of both.
- Two more reads per operation that makes new documents, for the comparison.
- Other archive serial numbers of merged documents stay with the trashed originals and are blocked until the trash is emptied; Paperless used to clear them.
- Between the last read and the trashing another user can still change a document; trash and versions make that reversible.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Leave it to Paperless, and say so: rejected by the owner.
- A `finish` tool that repeats the checks later: rejected by the owner (decision 12 of the design); the next calls are named instead.
<!-- SECTION:ALTERNATIVES:END -->


## Enforcement

- `tests/services/test_pdf_replacing.py`: every way the work can end, with the state afterwards.
- `tests/adapter/test_pdf.py`: no request asks for a deletion; parts and merges are told by their files. `docs/api-coverage.toml`: the excluded fields, checked by `tests/quality/test_api_coverage.py`.
- `tests/contract/test_pdf.py` and `tests/e2e/test_pdf.py`: on the fake and on Paperless 3.5.1.
