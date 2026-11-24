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
- **Verified** is: every expected task is known and succeeded, each names exactly one document, the document can be read, and its page count is the expected one. Parts are told by the number in their file name, not by their order. A merge also has to name every document in its file name, or have the sum of the pages; if neither can be told it is not verified.
- Right before trashing, each original is read again: its newest version and archive serial number must be the ones that were accepted. Nothing is trashed later than 45 seconds after the operation was started.
- **Archive serial numbers.** Where exactly one new document replaces one (delete_pages, edit_pdf with one output, merge: the metadata document's number, else the first), the number moves before the original is trashed. If it cannot move it is put back and nothing is trashed; if that fails too, number and documents are named. With several outputs, and for remove_document_password, the number stays with the trashed original. A new document that has a number already keeps it.
- Otherwise the originals stay; `message` says why, `trashed_ids` is what went, and `next` names the calls that finish the work. Nothing is remembered between calls.
- An older version, or first files, cannot replace a document with newer versions: the new document would not hold them.
- Supplements decision-6, decision-15 and decision-44; the confirmation still comes before the first request.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Where OCR takes longer than the wait, the original stays and one more call (`delete_document`) is needed; before, Paperless did it by itself later.
- A user without the right to delete is told so after the new documents were made, not before: the original stays and the new documents are named.
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
