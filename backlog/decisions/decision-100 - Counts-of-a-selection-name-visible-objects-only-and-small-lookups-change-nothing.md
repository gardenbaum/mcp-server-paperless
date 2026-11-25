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
- **Names are not shown through a template.** Paperless 3.5.1 checks only the right to view the document and puts the full name of its correspondent, type, storage path (with its template), tags, custom fields and owner into the template, also of those the user may not see (source, `templating/filepath.py`; contract test). Elsewhere Paperless gives such a user ids only, so `test_storage_path` reads what the user may see anew and refuses (names of kinds only) a document that refers to anything hidden, whatever the template holds, because a template language cannot be searched for names reliably. The owner counts as seen where it is the user, or where the user may list users.
- **`suggest_words` mirrors Paperless.** The index holds the words of the names of the correspondent, type and tags of a visible document, also of those hidden from the user, and cannot tell them apart from the text. The web UI's search box suggests the same words to the same user, and a search finds the documents by them. The tool description says so. The owner was asked whether this disclosure (words of hidden correspondent, type and tag names of visible documents, the same words Paperless's autocomplete gives) is accepted with the note in the tool description and here, and decided on 2026-11-26: "Alles wie von dir empfohlen." (everything as recommended by you); the recommendation was to accept it.
- Both live in the toolset `bulk`, beside `read_documents` (owner decision 13); read-only mode keeps them.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A user who may not list users cannot try a template on another user's document, and an object hidden or made between the check and the request is not noticed.
- The number of hidden objects is new to the user: Paperless's own web UI gets the same.
- The order of words with the same frequency is the index's.
- A template can be slow in Paperless's sandbox; the request ends with the client's timeout.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_read_documents.py`, `tests/services/test_lookups.py`, `tests/tools/test_read_documents.py`, `tests/tools/test_lookups.py`.
- `tests/adapter/test_lookups.py`; `tests/contract/test_lookups.py` on the fake and Paperless 3.5.1; `tests/e2e/test_lookups.py` over stdio and HTTP.
