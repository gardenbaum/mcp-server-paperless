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
- Compared before sending: the originals of documents (checksum filter, complete), the versions of the 5,000 most recently changed documents, the first 500 trash entries and 20 deleted versions among them, and every version of the document that gets one. Not compared: archive files, versions of trashed documents, trashed documents that are only shared with the user, and whatever the user cannot see. The bounds are in code, and every result says in `duplicates.not_checked` what was not read.
- After consumption the result, and `get_task` for an upload that was not waited for, report the duplicates Paperless itself finds for a document. For a version Paperless reports none, so this server compares the checksum and says so.
- A part of the check the user has no permission for is named and the rest runs. Any other failure stops the upload, also with `confirm=true`: an outage is never "no duplicates".
- A document the user cannot see is never named, also when Paperless rejects the file for it: no id and no title, the same answer as for one that no longer exists. That a rejection reveals that an equal file exists somewhere is Paperless' own behaviour. `get_task` and `list_tasks` do not show the existing document as one the task created or changed. The raw toolset shows what Paperless answers.
- Over stdio without upload directories, the descriptions of the two tools and the errors for refused content say that the operator can enable uploads by path (`PAPERLESS_MCP_UPLOAD_DIRS`), and a call that sends a path is answered with that instead of an argument error; the path is not repeated. Over HTTP nothing says so. decision-7 and decision-41 stand.
- Supplements decision-6, decision-12 and decision-15. No tool joins the default profile, so decision-34 and decision-42 are untouched.

## Consequences

- An upload costs up to 36 more requests (1 filter, 10 pages of versions, 5 pages of the trash with the text of its documents, 20 deleted versions); typical archives need three. Asking in a dialog reads twice.
- Two uploads of one file at the same time both pass the check; the report after consumption shows it.
- A file that Paperless changes before it stores it (pre-consume scripts, barcode splitting), and a checksum left from before 3.5 where the file was missing, are not found.
- One deleted version whose file Paperless can no longer read stops uploads until the trash is emptied.

## Alternatives

- Read all documents, or none, before sending: slow on large archives, or no check of versions; rejected by the owner.
- Report only after the upload: the second document then exists already.
- Upload unchecked when the check fails: rejected by the owner.

## Enforcement

- `tests/contract/test_duplicates.py`: what is read, on the fake and on Paperless 3.5.1, including hidden documents, the trash, archives and the checksum algorithm. `tests/adapter/test_duplicates.py`: the requests, and that a rejected task names no document of its own.
- `tests/services/test_duplicates.py`: each bound, each gap, outages, hidden documents. `tests/services/test_upload_duplicates.py`: nothing is sent without confirm or when the check fails, an acceptance covers exactly one file and its matches, rejection, `get_task`.
- `tests/tools/test_registry.py`, `tests/tools/test_confirmation.py` and `tests/tools/test_confirmation_text.py`: both tools take confirm, ask on both eras, stay non-destructive and quote titles and names. `tests/tools/test_path_hints.py`: the hint over stdio only.
- `tests/e2e/test_upload_duplicates.py` (live) and `tests/e2e/test_rejected_duplicates.py` (`just e2e-duplicates`, a stack that rejects duplicates).
