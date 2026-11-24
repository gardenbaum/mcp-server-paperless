---
id: decision-63
title: >-
  A kept document whose file is missing is refused while a copy has it, else
  named
date: '2026-11-24T10:14:50Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Duplicates are told by checksums, which Paperless keeps in its database when a file is lost. So `resolve_duplicates` could keep the document whose file Paperless cannot find and trash, or merge, the copy that still has it, and neither the preview nor the confirmation said so (PLMCP-110, found by the review of PLMCP-105).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- Before `resolve_duplicates` and `merge_documents_as_versions` change anything, the server reads the metadata of the file Paperless compares (the root version) and of the one a document shows (the newest): of the kept document, and of the others before a merge or if the kept one lacks a file. At most 5 reads at once and 30 seconds per check (as in PLMCP-105). A later version with the same checksum is the same file. An archive version alone is only named: Paperless makes it again from the original.
- `resolve_duplicates` refuses to keep a document that is known to lack the original while a duplicate of the call is known to have it, for both actions: in the preview as what would be refused, else before any confirmation; `confirm=true` does not get past it. A duplicate whose archive version is there counts if the kept document has neither. Everything else that is missing is named in preview, confirmation, result and failure, of the kept document as a warning.
- `merge_documents_as_versions` names it and does not refuse: there the files stay as versions, and giving a document whose file is lost a good one is a reason to use it.
- A check that cannot be made is said and does not block the trash, which can be restored. A merge is final, so there it is refused, with the reason, whichever document it is.
- The check is repeated in the confirmed call; the confirmation names every finding in full, so an acceptance does not cover another one (decision-44).
- Supplements decision-51, which stays as it is.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- One metadata read more per trash; before a merge one or two for each of at most 21 documents, again in the confirmed call. A trash whose kept document lacks a file does both checks, so up to 60 seconds.
- A merge can be refused because metadata is slow or fails.
- With a user who cannot see the copy that has the file, the loss is only named.
- A file that goes missing between the check and the request is not noticed. A file that is there but damaged counts as there; versions between the root and the newest are not looked at.
- A merged document without its file is named, not refused: the kept document then shows a missing file until that version is deleted.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Only naming it: a warning among much text, and nothing is gained by keeping the document without its file.
- Refusing in `merge_documents_as_versions` too: takes away the repair of a document without its file.
- Opening the files: would find damaged ones too, at the cost of reading all of them.
<!-- SECTION:ALTERNATIVES:END -->


## Enforcement

- `tests/contract/test_duplicates.py` and `tests/contract/test_document_versions.py`: on the fake and on Paperless 3.5.1 a lost file changes neither duplicates nor checksums, and a merge carries it along.
- `tests/services/test_kept_files.py`: refused, named, unchecked, the acceptance and the repeated check. `tests/tools/test_bulk.py` and `tests/tools/test_versions.py`: the tools. `tests/e2e/test_duplicates.py` and `tests/e2e/test_versions.py` (live).
