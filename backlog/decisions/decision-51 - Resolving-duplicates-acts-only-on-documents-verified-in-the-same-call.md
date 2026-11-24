---
id: decision-51
title: Resolving duplicates acts only on documents verified in the same call
date: '2026-11-24T02:17:28Z'
status: accepted
---
## Context

Paperless keeps duplicates and knows them (`duplicate_documents`, the filter `has_duplicates`), but no tool listed or resolved them. Merging documents as versions is final, so acting on documents that are no duplicates, or not any more, would lose data. The owner approved the design on 2026-11-23 (PLMCP-86): reading through the existing tools, one new tool in the opt-in toolset `bulk`, a duplicate with several versions may be trashed with a warning.

## Decision

- `search_documents` takes `has_duplicates`, and `get_document` has the section `duplicates`: the copies Paperless reports, live ones first, at most 25, with what tells them apart and what could not be read. The facts of copies in the trash come from one bounded read of the trash (decision-47: 500 entries, 64 MiB). Paperless tells there no notes, custom fields, page count or versions, because they are in the trash too; that is said, not guessed.
- `resolve_duplicates` (toolset `bulk`, destructive, `dry_run`, `confirm`) keeps one document and either moves named duplicates to the trash or merges them into it as versions. It acts only on documents that Paperless takes for duplicates of the kept one in the same call, outside the trash and visible to the user. Equality through archive files is not transitive, so each is compared with the kept document.
- Whole or refused: an id that is no such duplicate, a document the user may not change and, for a merge, a duplicate with several versions refuse the call before anything is sent, all of them named. A document the user cannot see is told like one that does not exist.
- The confirmation names every document with its notes, tags, custom fields, versions and archive serial number, says where the newest file is not the one compared or only an archive file matches, which other duplicates stay (10 named, the rest counted) and what is final: the trash can be restored, a merge cannot. Its scope holds the action, the kept document and each duplicate as they were read (all metadata, `modified`, every version and checksum; not who else may view or change it, which the read does not tell), and all other duplicates. `modified` alone would not do: the bulk edits of Paperless leave it. The confirmation of `merge_documents_as_versions`, which is as final, binds the same.
- After the request the state is read again, also after a failure, a timeout or a task that cannot be followed. A duplicate counts as resolved only if Paperless shows it in the trash as a duplicate of the kept document, or as a version of it; every other one is named as still there or unknown, because what is not found may only be hidden. A kept document that is gone then is said first and loudly (`kept_seen`).
- The confirmation of `merge_documents_as_versions` says too what becomes of archive serial numbers: Paperless 3.5.1 gives the first one to a kept document without one and releases the others.
- Supplements decisions 6, 15, 44 and 47. No tool joins the default profile, so decision-34 and decision-42 are untouched.

## Consequences

- A note or version that is added to a duplicate between the read and the request goes with it. From the trash it can be restored; a merge is final. The check runs on the read that is written, the smallest window the API allows.
- Two crossed calls (keep A and trash B, keep B and trash A) can leave neither outside the trash. Paperless offers no conditional write; the answer says that the kept document is gone and to restore one.
- The checks before a merge and the read after it exist twice, here and in `VersionService.merge`: they name documents differently (all refused ones at once, each state afterwards), only the effect text and what is bound are shared.
- Binding all metadata asks again when anything about a document changed while the dialog was open, also what the text does not show.
- A duplicate with a version in the trash cannot be merged: Paperless refuses it, and the refusal changes nothing. This server does not look for it beforehand.
- Ownership and the delete permission are known only from Paperless' refusal, which is all or nothing.
- Later versions are not compared: Paperless does not.
- The list of toolsets that are off names one more tool: `OVERVIEW_GUIDE_LIMIT` rises by its 21 bytes, as approved.

## Alternatives

- A tool in the default profile: the 20 places are taken (decision-34); rejected by the owner.
- Telling the model to use `bulk_edit_documents` and `merge_documents_as_versions`: neither checks that the documents are duplicates.
- Resolving whole groups found by the server: which copy to keep is the user's choice.

## Enforcement

- `tests/contract/test_duplicates.py` and `tests/contract/test_document_versions.py`: the filter, the trash, archive files, versions, hidden documents and archive serial numbers on the fake and on Paperless 3.5.1. `tests/adapter/test_trash.py`: the one request.
- `tests/services/test_duplicate_list.py`: what is listed, differs and is left out. `tests/services/test_duplicate_resolution.py`: whole or refused, hidden like missing, the scope, failures, and the words (snapshot). `tests/services/test_versions.py`: archive serial numbers.
- `tests/tools/test_bulk.py`, `tests/tools/test_registry.py`, `tests/tools/test_confirmation.py` and `tests/tools/test_confirmation_text.py`: the tool, its dialog on both eras and hostile titles. `tests/e2e/test_duplicates.py` (live).
