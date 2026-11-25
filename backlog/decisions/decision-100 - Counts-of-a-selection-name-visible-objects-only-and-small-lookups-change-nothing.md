---
id: decision-100
title: >-
  Counts of a selection name visible objects only, and small lookups change
  nothing
date: '2026-11-25T15:02:38Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-83 AC 1 wants selection data, autocomplete and the storage path test covered. On 2026-11-24 the owner approved the design of PLMCP-83 with all recommended options: asked "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?" (do you approve the drafts with all recommended options, with the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010?), the owner chose "Alle wie empfohlen" (all as recommended).

Probed on Paperless 3.5.1 (CI run 45638730209, job 139291151398 "e2e (1/6)", a temporary test of PLMCP-83.2):

- **selection_data** answers every object of the instance, also those the user may not see, with 0 or a count. It needs only the right to view documents. A document that does not exist or is named twice is a 400, a hidden one a 403. A value taken off a document is not counted.
- **autocomplete** folds case and accents on both sides; `limit=0` is a 400, `limit=abc` a server error, a term with a space finds nothing. Words come from documents the user sees.
- **storage_paths/test** needs only the right to view documents. Undefined variables, a syntax error, an unsafe path, an attribute of nothing and a long loop all give null, which comes as an empty body. An empty result is an empty text. A leading slash is cut. The old style `{title}` is not converted. A hidden document and one that does not exist look the same (400).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **`read_documents(counts=true)`** counts the whole selection (ids, filters or view, at most 1,000), whatever page is asked for. Only objects whose name the user sees are named; of the others only the number is told (`hidden`). The lists are read anew: from an older list a new object would count as hidden. Most frequent first, 10,000 characters a kind; what has no room is counted in `more`.
- With `counts` no rows are read unless `fields` is given. No name is looked up after the 40 seconds of the call: then no counts at all, and a notice. A document that changes meanwhile is an error, not a part of the counts.
- **`suggest_words`** takes the beginning of one word and 1 to 50 words; a word over 100 characters is left out and counted.
- **`test_storage_path`** takes exactly one of a stored path (name or id) and a template of at most 2,000 characters, and answers the path (at most 1,000 characters) or null with a note. It is a POST that changes nothing, so it has the read-only hint.
- Both live in the toolset `bulk`, beside `read_documents` (owner decision 13); read-only mode keeps them.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The number of hidden objects is new to the user: Paperless's own web UI gets the same.
- The order of words with the same frequency is the index's.
- A template can be slow in Paperless's sandbox; the request ends with the client's timeout.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_read_documents.py`, `tests/services/test_lookups.py`, `tests/tools/test_read_documents.py`, `tests/tools/test_lookups.py`.
- `tests/adapter/test_lookups.py`; `tests/contract/test_lookups.py` on the fake and Paperless 3.5.1; `tests/e2e/test_lookups.py` over stdio and HTTP.
