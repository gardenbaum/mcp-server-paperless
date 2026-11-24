---
id: decision-72
title: >-
  Left out of bulk_edit: single-tag and deprecated methods, set_permissions, and
  Paperless's own selections
date: '2026-11-24T15:25:43Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
AC 5 of PLMCP-80 wants every method of `bulk_edit` and `bulk_edit_objects` covered, and an omission only with the owner's approval, quoted with its date. The design named the omissions as its decisions 2, 12 and 13. On 2026-11-24 the owner approved them: "Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits." (draft 80: numbers 1 to 20 as you recommend, with the limits named there) (PLMCP-80).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Left out, owner-approved as quoted above (2026-11-24):

- `bulk_edit` methods `add_tag` and `remove_tag`: `modify_tags` does both.
- `bulk_edit` method `set_permissions`: the tool of that name does it per document with a diff; this form without `owner` removes the owner (read in the 3.5.1 source).
- The deprecated `bulk_edit` forms `rotate`, `merge`, `edit_pdf`, `remove_password`, `split`, `delete_pages`, `delete` and `reprocess`: their own endpoints are used.
- `all`, `filters` and `excluded_documents` of `bulk_edit`, `documents/delete` and `bulk_download`: decision-69 resolves filters here; Paperless names nobody and takes a filter it does not understand as none.
- `all` and `filters` of `bulk_edit_objects`: `list_taxonomy` with `name_contains` shows the names with their document counts.

`run_workflow` is not left out: it comes with PLMCP-80.4. `from_webui` stays with PLMCP-83.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- No ability is lost for the user: each omission is another way to something a tool does.
- The exclusions are rows of `docs/api-coverage.toml` and name this decision.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_api_coverage.py`: a row needs the quote and the date from this file.
- `tests/adapter/test_bulk.py`: the endpoints that are used. `tests/e2e/test_bulk.py`: Paperless takes a filter it does not understand as none.
