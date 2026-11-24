---
id: decision-71
title: >-
  Bulk changes name every document, bind the current values and refuse an
  exclusion that is not there
date: '2026-11-24T15:25:42Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
bulk_edit_documents took ids only; its confirmation named three titles and a count and bound the ids and the change. A selection by filter needs more: what the user agreed to must be exactly what happens. decision-69 left two things to the tools that change documents: what happens to `missing`, and to an exclusion the user's list does not hold (a deleted object excludes nothing, a hidden one does; they cannot be told apart).

The owner approved on 2026-11-24 the design, with numbers 1A, 2a, 3a, 4a and 5 (every document named, current values bound): "Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits." (draft 80: numbers 1 to 20 as you recommend, with the limits named there) (PLMCP-80). The handling of the exclusions was left to this task: a wrong set is data loss, and restricted users must not be refused for a kind they may not list.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Every document is named.** The confirmation names each document the call changes (id and quoted title, at most 100), each other document a mirrored link changes (at most 100, the rest counted; also one that is selected but only changed by the mirroring), and so do the refusals for documents the user may not change. It also names the new custom field values (texts quoted and cut like titles, select labels, linked documents), every tag and field, and the parent tags that come along. This holds for ids, too.
- **Current values are bound.** An accepted dialog covers the documents in their order, the current values of the fields the operation touches, the mirrored documents, the change with its resolved ids, and the newest file versions where files are used. `modified` cannot serve: bulk changes leave it as it is.
- **A filter or a view alone is never confirmed by `confirm=true`.** A client with dialogs shows the named documents; after the yes the selection is resolved again and only the same set with the same values runs. Any other client gets the refusal, which names the next call: the same filter plus `document_ids` and `confirm=true`. That call is refused if one no longer matches; what matches besides is named (`also_matching`) and not touched. Where dialogs are required (decision-53) there is no second call.
- **At most 100 documents change per call**, of at most 1,000 matches. Those that already are as asked are skipped, so the same call goes on; `remaining` counts what is left. Reprocessing never becomes "as asked": more than 100 matches of a filter are refused.
- **Exclusions.** `missing` ids refuse the call (as before). `excluded_absent` (a listable kind whose list lacks the object) refuses every change and its preview, also with ids, and names the way on: ids without the filter. `excluded_unchecked` (a kind the user may not list) does not refuse; the confirmation and the preview say that it could not be checked.
- **After a failed task, a lost answer or an unconfirmed task** the documents are read again, once, and each is reported as as asked, not changed or unknown, in the error text or in `outcomes`. HTTP 429 ends the call: also when the task list answers it (after the client's own retries), the task is given up at once as unconfirmed and nothing is read again. Nor is it after a refusal, which changed nothing. While the outcome is open the answer does not say to go on, and `remaining` counts what was not seen changed. Other tools wait a 429 out as before, because they need what the task names. A read that fails makes them unknown and never hides the first error. What reprocessing did is always unknown.
- Moving to the trash and reprocessing use `documents/delete/` and `documents/reprocess/` (the same checks; the `bulk_edit` forms are deprecated in API version 10).
- Supplements decisions 6, 12, 15, 44, 53, 60 and 69. Supersedes nothing.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A restricted user whose view excludes a tag that is hidden from them is refused although the set would be right. They can pass the ids that read_documents shows.
- Texts of about 7.5 KB for 100 documents with ordinary titles; 24,281 characters were measured for one list with hostile titles, where every character is escaped (the length handling of decision-64). That is not the maximum: with a link change there are three lists of up to 100 documents (the linked, the changed and the mirrored ones), about 70 KB estimated, and a long list of tags or fields the caller names adds to it.
- A parent tag the user cannot see is named by its number; its own parents cannot be named, although they come along.
- With ids and a filter (no dialog) the current values are not bound, only that the documents still match: the server keeps nothing between calls.
- "As asked" after a delete means no longer listed: someone else may have hidden the document at the same moment.
- Between the last read and the request another user can still change a document (Paperless has no conditional write).
- An unrelated tag added to a document while the dialog is open asks again.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/services/test_bulk_selection.py` (marker decision-71): as a property, no call by filter or view alone writes; what joined, left or changed while a dialog was open; the limits 100/101 and 1,000/1,001; exclusions; every outcome. `tests/services/test_confirmation_scope.py`: what an acceptance binds.
- `tests/tools/test_bulk_dialogs.py`: both protocol eras, declined, changed meanwhile, required dialogs. `tests/tools/test_bulk.py`: the call a refusal names goes through.
- `tests/contract/test_bulk.py` and `tests/e2e/test_bulk.py`: the endpoints, a document that stops matching, a restricted user, a filter Paperless does not understand, on Paperless 3.5.1.
