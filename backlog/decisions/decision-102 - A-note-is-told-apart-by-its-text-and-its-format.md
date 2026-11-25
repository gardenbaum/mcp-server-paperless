---
id: decision-102
title: A note is told apart by its text and its format
date: '2026-11-25T15:36:50Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Paperless 3.5.1 stores with each note how it is shown: `plain` (default) or `markdown`. decision-73 skips a document that already has a note with the same text. On 2026-11-24 the owner approved the design of PLMCP-83 with all recommended options ("Alle wie empfohlen", all as recommended); its section 3 says that `add_notes`' "same text already there" compares the format too.

Probed on 3.5.1 (CI run 45638730209, job 139291151398): a note without a format is plain, another value is a 400, and the format comes in the document row and in the notes.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-73, which otherwise holds unchanged: a note is there already when its text and its format are the same. The format is one per call of `add_note` and `add_notes`, plain by default and always sent; it is part of what a confirmation binds, and a markdown note is called so in it. A note whose answer was lost is looked up by text and format.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The same text can stand twice on a document, once plain and once as markdown.
- Notes of different formats take two calls of `add_notes`.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_bulk_notes.py` ("the format"), `tests/adapter/test_notes_and_trash.py`, `tests/contract/test_notes_and_trash.py` on the fake and Paperless 3.5.1, `tests/tools/test_bulk_items.py`, `tests/e2e/test_bulk_items.py`.
