---
id: decision-55
title: A trash filter only shows; what became of each entry is read afterwards
date: '2026-11-24T08:02:51Z'
status: accepted
---
## Context

The trash API has only pages. PLMCP-78 narrows the list and lets restoring and deleting for good take a narrowed list that is shown first. Paperless checks all ids of a request before it changes any, but the restore that follows is no transaction, and emptying logs and swallows every error and answers OK. The owner approved the design on 2026-11-23: filters only show, acting takes shown ids; a blocking id is named where Paperless lets it be known, otherwise it is said to be unknown; a cap of 1,000 entries with `truncated`; the trash is read again after emptying.

## Decision

- Filters (title, document type, kind, day of deletion in UTC, ids) are applied here, on one request of up to 1,000 entries, because pages of the trash overlap on PostgreSQL. That request has the size limit of decision-47, 64 MiB; a larger one, and one that fails for now (timeout, 502, 503, 504, connection), is made again with 100 entries and the answer is `truncated` if there are more. Days are compared in UTC. Versions are shown and matched with the title and document type their document has now; their owner is that of the upload. Only `ids`, and ids to act on, are looked for beyond it, page by page.
- A filter never acts by itself. `restore_from_trash` with a filter restores nothing and names the matches and their ids. `empty_trash` with a filter asks through `ConfirmationRequired.require` whether or not `confirm` is true, names every match and binds the ids in the scope (decision-44): it runs only after the user accepted exactly these in a dialog; without a dialog the next call names the ids. At most 100 entries are acted on, the newest deletions first, and the rest is counted. An empty filter is refused.
- Deleting for good needs confirm, restoring does not: unchanged (decision-6, decision-15).
- A confirmation may carry another hint than "call again with the same arguments", because here the arguments change.
- Before the request every id has to be listed; each that is not is named, the first ten with the reason where `/root/` and the document tell it (not deleted, a version of a trashed document, in the trash but not found in the list). When Paperless refuses all the same, the ids that are no longer listed are named, or it is said that none is known.
- After the request, also after a failed one, the trash is read again and each entry is reported as restored or deleted, `still_in_trash` or `unverified`. An entry that left the list counts as restored only if it is seen outside the trash, and as deleted only if it does not answer; up to 100 entries are looked up one by one for that. A failure of the request beside a success is told; a failed look gives `unverified` and never hides what was done.
- Where the operator requires dialogs (decision-53), a filter and ids run only after an accepted dialog, and the refusal names no other way.
- Supplements decisions 6, 15, 44, 47 and 53.

## Consequences

- Breaking: the output field `restored` of `restore_from_trash` is now `entries`, each with an `outcome`, because an entry it lists may not have been restored. `empty_trash` gains `entries`; its `document_ids` and `count` hold only entries seen to be gone.
- Restoring by a filter costs a second call although it needs no confirmation.
- "All or none" is Paperless's check, not a transaction; descriptions and answers say so.
- A filter cannot see beyond the cap. On PostgreSQL an id beyond it may be missed by the pages; it is then refused as not found, not acted on.
- Restoring and emptying read up to 64 MiB of trash before and after. A proxy that compresses makes filters fail with that cause. Ids still work through pages of 100 entries read with the ordinary request of `list_trash`, which had no size limit before either.

## Alternatives

- Acting by a filter at once and reporting it: rejected by the owner.
- Several small pages: rows repeat or are skipped.
- Splitting a refused batch to find the blocking id: that would change data.

## Enforcement

- `tests/services/test_trash_filters.py`: each filter, the cap, large and compressed answers, that a filter writes nothing with or without confirm, what an acceptance covers, the blocking ids and every outcome. `tests/tools/test_trash.py`: the same through the tools, with dialogs.
- `tests/quality/test_confirmation_sites.py`: the scope. `tests/tools/test_confirmation_text.py`: hostile titles. `tests/contract/test_trash.py` and `tests/e2e/test_trash.py` on Paperless 3.5.1.
