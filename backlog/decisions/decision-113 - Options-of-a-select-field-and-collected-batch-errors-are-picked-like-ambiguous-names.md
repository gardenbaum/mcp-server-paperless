---
id: decision-113
title: >-
  Options of a select field and collected batch errors are picked like ambiguous
  names
date: '2026-11-26T10:36:04Z'
status: accepted
---

## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-109 (accepted) lets the user pick among the matches of an ambiguous name. Part 2 of PLMCP-100 extends the pick to the options of select custom fields and to `update_documents`, which collects what is wrong with each item. decision-109 stays as accepted; this records what part 2 adds and the one place where it changes decision-109. The design of PLMCP-100 was approved by the owner on 2026-11-24: "Alle wie empfohlen" (all as recommended). The owner asked on 2026-11-26 to fix and merge part 2: "Bitte fixe 190 und 189 und merge danach." (Please fix 190 and 189 and merge them afterwards.)
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- Two options of a select field with one label are matches like two objects of one name, for a document's value and for an option to rename or remove. The dialog names label and place in the list, and so does a confirmation of one of them. The topic of such a question is the field by its id, so renaming the field while the user chooses does not make it another question. Two renames of one option are refused.
- `update_documents` collects what is wrong with its items: it asks, for the first question, only if every refused item is ambiguous.
- On 2026-07-28, of a longer error (a batch) the first 8000 characters and the hint travel in the sealed state of decision-44, and the rest is counted: the error after a no is then cut there, while a client that is not asked, or that waits on a handshake connection, gets all of it.
- Supersedes one sentence of decision-109's consequences, "An error without a question, such as one that lists the errors of several items, asks nothing.": an error without a question, such as a list of errors of which one is something else, asks nothing. Everything else of decision-109 holds.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Names that look alike once cut carry their id in the refusal of `update_documents`, which makes its worst refusal 14 characters longer: the owner approved the limit 5402 on 2026-11-26 ("Grenze auf 5402").
- A user who declines a batch dialog may get a cut error; the count of what was left out is shown.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Asking for every ambiguous item of a batch: up to five dialogs for one call, and the other errors would wait behind them.
- Topics by the field's name: a rename during the dialog would let the remaining option replace the pick.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/tools/test_choices.py`, `tests/services/test_custom_values.py`, `tests/services/test_taxonomy_edits.py` and `tests/services/test_bulk_updates.py`: options, renames and collected errors.
- `tests/domain/test_choices.py`: the topic of an object is its id.
