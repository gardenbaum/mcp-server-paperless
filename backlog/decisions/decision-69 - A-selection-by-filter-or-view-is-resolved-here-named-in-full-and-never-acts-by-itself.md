---
id: decision-69
title: >-
  A selection by filter or view is resolved here, named in full and never acts
  by itself
date: '2026-11-24T13:44:27Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-80 lets a caller choose documents by a search or a saved view (AC 2). Paperless can do that itself: `bulk_edit`, `documents/delete`, `rotate` and others take `all`, `filters` and `excluded_documents`. But Paperless resolves them when the request arrives and names nobody, its tag and reference filters take a value that is no list of numbers, or an empty one, as no filter at all (read in the 3.5.1 source), and `all` without `filters` is every document: a typing mistake would change the archive.

The design was put to the owner with the alternatives (decisions 1 to 4 of the design). On 2026-11-24 the owner approved: "Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits." (draft 80: numbers 1 to 20 as you recommend, with the limits named there) (PLMCP-80).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- Documents are chosen by ids (1 to 100), by the filters of `search_documents` (the same names, types and meaning, as one strict object: an unknown key is refused) or by a saved view. One service resolves them for every tool that takes a selection. Paperless's `all`, `filters` and `excluded_documents` are not sent.
- One request with the ordinary row fields, uncompressed, at most 64 MiB (as decision-47 reads the trash). At most **1,000** documents may match; more is refused with the count. An answer with fewer rows than it counts is refused.
- Every match is named. Refused always: nothing chosen, filters and view together, `exclude_ids` without either, a filter object without a filter, an empty value (it would match everything). Refused for a change, and noticed when reading: a view that filters nothing, and a full-text query with syntax the index misreads.
- **It never acts by itself.** A filter or a view is resolved anew in each call and what it matches is shown. Ids together with the filters or the view are exactly these documents, and the call is refused, naming them, if one no longer matches; what matches only now is named and not touched. The dialogs and the second call of the tools that change documents are built on this in PLMCP-80.2 and 80.3.
- Ids alone stay possible: the caller names the documents, and one that is missing or hidden is named.
- `rotate_documents`, `reprocess_documents`, `bulk_download`, `set_permissions` and sharing take no filter of their own: `read_documents` shows the ids.
- Supplements decisions 6, 15, 44 and 53, and follows the same principle as decision-55. Supersedes nothing.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A matched set is compared by what it matches, not by the text of the filter or the rules of the view: an edited view with the same matches passes.
- A document another user changes between the check and a write is not protected (Paperless has no conditional write).
- A full-text match comes from the search index, which can be older than the document.
- A selection reads up to 1,001 rows in one request.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/services/test_selection.py` (marker decision-69): limits (1,000 and 1,001, 64 MiB), every refusal, a set that changed (also as a property), a restricted user.
- `tests/contract/test_selection.py`: every filter on the fake and on Paperless 3.5.1 gives what a search gives; `tests/adapter/test_selection.py`: the request.
- `tests/tools/test_read_documents.py`: the filters equal those of `search_documents`; `tests/e2e/test_read_documents.py`: 1,000 and 1,001 documents, a document that stops matching, a restricted user, on Paperless 3.5.1.
