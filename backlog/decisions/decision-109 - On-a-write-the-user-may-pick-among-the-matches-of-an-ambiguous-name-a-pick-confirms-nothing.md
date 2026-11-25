---
id: decision-109
title: >-
  On a write the user may pick among the matches of an ambiguous name; a pick
  confirms nothing
date: '2026-11-25T17:53:00Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-5 never guesses: an ambiguous name is an error that lists the matches. The assistant then guesses an id or asks in free text, which costs a turn and can file a document under the wrong object. Form elicitation offers titled options for this, on both protocol eras. The design of PLMCP-100 was approved by the owner on 2026-11-24 with all its recommendations, in the question dialog "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?" (Do you approve the designs 81, 87, 88, 89, 90, 83 and 100 with all recommended options, including the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010?), answer "Alle wie empfohlen" (all as recommended). This part changes none of those limits.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- A tool that writes (it is not read-only by its annotations) and finds a name ambiguous shows the matches in a dialog, where the client can show one, and goes on with the one the user picks. The resolver still never picks. Reads, resources, prompts and calls with `dry_run` are never asked; they and clients without forms get the error with its list, as before.
- Asked is only what has 2 to 20 matches, at most five times per call, one name after the other, and never once the run has sent anything but GET or HEAD to Paperless.
- The form has one required field without a default. Clients from 2025-11-25 on get `oneOf` with titles, older ones `enum` and `enumNames`. A title holds the name, quoted and cut like every text of others in a dialog (decision-44), the id, the number of documents where there is one, and the owner: Paperless holds one name once per owner, so that is what tells two of them apart. The owner's name is read from the list of users; who may not read it sees the id.
- Only an accepted answer with exactly the field `choice` and one of the offered values is a pick. Everything else (decline, cancel, anything malformed, a timeout, a replayed or expired state, a client that cannot show it, a dropped connection) ends the call with the error the user would have got without the dialog, without running the tool again: meanwhile the name may be unique, and the call would write. Only after a declined or closed dialog one sentence more says that the user picked none and the assistant must not.
- A pick answers exactly its question: kind, word and every match with its title and owner. Other matches are another question. If the name became unique while the user was asked and is not what they picked, the call fails.
- A pick confirms nothing. The confirmation of decision-44 is asked for afterwards, for the picked object; `confirm=true` picks nothing. A pick asked for after an accepted confirmation takes that acceptance back. Under decision-53 a pick that is not made is no `NotConfirmed`.
- On 2026-07-28 the picks and the error travel in the sealed state of decision-44, which any answer uses up.
- Two options of a select field with one label are matches like two objects of one name, for a document's value and for an option to rename or remove; their title holds the label and the place in the list. `update_documents` collects what is wrong with its items: it asks, for the first question, only if every refused item is ambiguous.
- Supplements decision-5 and decision-44; decision-53 is unchanged.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A call with five names and three confirmations takes eight dialogs, and each runs the reads of the call again; FastMCP's client gives up after ten.
- A client that answers dialogs by itself picks by itself. The confirmation is asked separately.
- A POST that only reads counts as a write here; after one, nobody is asked.
- The names may be up to the age of the cache old (300 seconds). An object that is gone then fails as with its id.
- Naming an owner reads the list of users (cached like every list) before the dialog. If that fails, the id is shown.
- An error without a question, such as a list of errors of which one is something else, asks nothing.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- One form with a field per name: every tool would have to collect all its ambiguities first.
- Asking on reads: it interrupts more than it helps.
- The plain error after a no: the assistant could pick an id itself right after the user closed the dialog.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/domain/test_choices.py`, `tests/services/test_taxonomy.py`: questions, picks, what contradicts them.
- `tests/tools/test_choices.py`: every place of every write tool that takes a name (found in the schemas) asks and writes exactly what the call with the id writes; every place of every read tool never asks; every way a dialog ends, on both eras. `tests/test_choices_http.py`: the same on the wire, other callers, older clients, dropped connections. `tests/e2e/test_choices.py`: both entrypoints and eras against Paperless.
- `tests/adapter/test_client.py`, `tests/fakes/test_paperless.py`: what counts as a write.
