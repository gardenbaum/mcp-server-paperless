---
id: decision-47
title: Uploading a file Paperless already holds needs confirm
date: '2026-11-23T23:25:27Z'
status: accepted
---
## Context

Paperless keeps a file it already holds as a second document, unless it is configured to reject duplicates. The upload tools did not look, and a model repeats uploads easily. Only the original uploads of documents can be found by checksum; versions and the trash have no filter. The owner approved the design on 2026-11-23 (PLMCP-77): a bounded scan of versions, confirm for a known duplicate, no upload when the check cannot run, and no id of a document the user cannot see.

## Decision

- `upload_document` and `upload_document_version` compare the SHA-256 of the file with what the user can see before anything is sent. A match needs `confirm`, asked through `ConfirmationRequired.require` (decision-44) with the checksum, the number of matches and up to 100 original documents plus every other match in the scope. The tools stay non-destructive: the match is the one case that asks.
- Compared before sending: the originals of documents (checksum filter), the versions of the 5,000 most recently changed documents, the first 500 trash entries and 20 deleted versions among them, and every version of the document that gets one. Not compared: archive files, versions of trashed documents, trashed documents that are only shared with the user, and whatever the user cannot see. The bounds are in code, and every result says in `duplicates.not_checked` what was not read.
- A scan that does not add up is a failed check, not a complete one. Versions are read by descending id, which edits do not move: every document the first page counted has to be among those read, and what came since has to have higher ids than all of them, so additions do not fail the check and a restored or newly shared older document does. Above 5,000 documents the order is last changed first, id as tie-break, and the gap says that a document changed meanwhile may be missed. The originals are looked up before and after the versions, because a document merged into another one in between is neither. A page that is gone, and a checksum that Paperless does not tell, are a failed check. A check that does not add up runs once more and then fails, and the error names the cause and that nothing skips the check.
- The trash is read in one request of up to 500 entries and counted again at the end. Paperless orders it by a date only, so on PostgreSQL its pages overlap in every pass (seen on PostgreSQL 17 in the review), which small pages cannot repair. Because the rows carry the whole text, the bound of that request is its size: 64 MiB, enforced while the uncompressed body is read; a compressed answer is refused, and it is parsed in a thread. A trash larger than that is a failed check with its true cause. This replaces an earlier bound of 25 entries per page.
- The whole check has 25 seconds and fails after them: many clients give a call 60 seconds, so that it, a wait of 30 seconds and the sending of an ordinary file fit.
- Departure from decision-44, which wants every target behind "and 5 more" in the scope: beyond 100 original documents the scope holds the SHA-256 and the count, not the ids. The action is the sending of exactly this file, whichever documents already hold it.
- After consumption the result, and `get_task` for an upload that was not waited for, report the duplicates Paperless itself finds for a document. For a version Paperless reports none, so this server compares the checksum and says so.
- A part of the check the user has no permission for is named and the rest runs. Any other failure stops the upload, also with `confirm=true`: an outage is never "no duplicates".
- A document the user cannot see is never named, also when Paperless rejects the file for it: no id and no title, the same answer as for one that no longer exists. That a rejection reveals that an equal file exists somewhere is Paperless' own behaviour. `get_task` and `list_tasks` do not show the existing document as one the task created or changed. The raw toolset shows what Paperless answers.
- Over stdio without upload directories, the descriptions of the two tools and the errors for refused content say that the operator can enable uploads by path (`PAPERLESS_MCP_UPLOAD_DIRS`), and a call that sends a path is answered with that instead of an argument error; the path is not repeated. Over HTTP nothing says so. decision-7 and decision-41 stand.
- Supplements decision-6, decision-12 and decision-15. No tool joins the default profile, so decision-34 and decision-42 are untouched.

## Consequences

- An upload costs up to 35 more requests (2 filters, 11 pages of versions, 2 reads of the trash, 20 deleted versions), twice that if documents move; typical archives need five. An archive too slow for 25 seconds, or a trash over 64 MiB, cannot be uploaded to.
- Two uploads of one file at the same time both pass the check; the report after consumption shows it.
- A file that Paperless changes before it stores it (pre-consume scripts, barcode splitting), and a checksum left from before 3.5 where the file was missing, are not found.
- A document that leaves (deleted, or no longer shared) and another one that appears (restored, or newly shared) during one scan can cancel out in all counts; that is not noticed. Only the checksum of a deleted version is read, so a missing file does not stop uploads.

## Alternatives

- Read all documents, or none, before sending: slow on large archives, or no check of versions; rejected by the owner.
- Report only after the upload: the second document then exists already.
- Upload unchecked when the check fails: rejected by the owner.

## Enforcement

- `tests/contract/test_duplicates.py`: what is read, on the fake and on Paperless 3.5.1, including hidden documents, the trash, archives and the checksum algorithm. `tests/adapter/test_duplicates.py`: the requests, and that a rejected task names no document of its own.
- `tests/services/test_duplicates.py`: each bound, each gap, outages, hidden documents. `tests/services/test_upload_duplicates.py`: nothing is sent without confirm or when the check fails, an acceptance covers exactly one file and its matches, rejection, `get_task`.
- `tests/tools/test_registry.py`, `tests/tools/test_confirmation.py` and `tests/tools/test_confirmation_text.py`: both tools take confirm, ask on both eras, stay non-destructive and quote titles and names. `tests/tools/test_upload_confirm.py`: confirm and the dialog in every form (content and path). `tests/tools/test_path_hints.py`: the hint over stdio only.
- `tests/e2e/test_upload_duplicates.py` (live) and `tests/e2e/test_rejected_duplicates.py` (`just e2e-duplicates`, a stack that rejects duplicates) and `tests/e2e/test_trash_order.py` (`just e2e-postgres`, a trash of few dates on PostgreSQL).
