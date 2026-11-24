---
id: decision-48
title: >-
  Changing the text of a document needs confirm and the state of the text that
  was read
date: '2026-11-24T00:42:14Z'
status: accepted
---
## Context

The text of a document (OCR) can be wrong or missing, and search and the AI read it. decision-12 left content out of the confirmed changes because no tool changed it. A PATCH of `content` replaces the text of one version as a whole, has no precondition (no ETag, no conditional write). Read in the source of 3.5.1 and not tested here: Paperless keeps the old text only in its audit log, if that is on, and runs the workflows for updated documents. Paperless also accepts `version` for a version in the trash, and answers 200 without writing if `fields` does not hold `content`. The owner approved the design on 2026-11-23 (PLMCP-75); the main agent decided the toolset, the short preview with a full `dry_run` and the documented window, and reported that to the owner.

## Decision

- Changing the text of a document needs `confirm`. Supplements decision-12, which stays as it is for classification and owner, and decision-6 and decision-15.
- It is a destructive tool of its own, `correct_document_text`, not parameters of `update_document`: that tool is annotated non-destructive, so clients may approve it without asking.
- Every call carries `text_state`, the fingerprint of document, version and whole text that `get_document_content` returns. A mismatch is a conflict and writes nothing, in the two-step flow and after a dialog alike. The write always names the version, so a version added meanwhile never receives the text. The text is read right before the write and again right after it, and a text that is not the one sent is reported.
- A passage must occur exactly once, overlapping occurrences counted; there is no fuzzy match (as in decision-5). Passage and replacement hold at most 20,000 characters, a whole text 1,000,000, and the request 2,500,000 bytes, below Paperless' own limit.
- The confirmation shows up to 300 characters of the old and of the new text with 40 characters around them, a longer change only at both ends (150 characters each) with the number of characters left out. The excerpts stand last, after every consequence and number, in JSON quotes. Escaped are control, format, separator (but the plain space), unassigned and default-ignorable characters, signs that show as nothing, every space after a space, a combining mark without a letter before it, and what can be read as a double quote (typographic, full-width, ornamental, primes, two single quotes side by side): what differs only in whitespace or hidden characters is visible, and the text cannot seem to close its quotes. Single apostrophes and guillemets stay, because nothing the server writes looks like one. The excerpts show the text that would be stored, compared with the one there is. This supplements the short excerpts of decision-15 and the cut of 60 and 80 characters of decision-44, for this one tool; 60 characters cannot show a correction. Error messages never repeat the arguments, and results hold no text but that of a dry run.
- `dry_run=true` returns the whole change as data, in parts of 10,000 characters of each side, so a long change is never confirmable only from an excerpt.
- The scope of the confirmation (decision-44) holds the version, the text that was read and the exact text that would be stored.
- An empty text is allowed; the confirmation says so.
- The tool is in the opt-in toolset `versions`. Weighed against decision-34 and decision-42: a rare, confirmed write does not justify a place in the default profile, whose budget of 20 tools is used and stays. Reading the text of a version extends `get_document_content` in `core`.

## Consequences

- A correction is a read and two calls, or a read, a call and a dialog. Without the toolset `versions` the text cannot be corrected.
- Between the read and the PATCH (one round trip) another writer can still be overwritten; Paperless offers no conditional write. A reprocessing that already runs overwrites the correction later and cannot be detected.
- Reprocessing overwrites a correction; a new version or PDF edit has its own text. Search finds the text of the newest version only. The workflows for updated documents run (3.5.1 source), and one that matches content can act on the new text.
- Paperless drops leading and trailing whitespace of the root version's text; the confirmation says so when it happens. Whitespace that goes this way around a correction is named there, not shown as part of the change.
- Text in which German or English quotation marks occur shows them as escapes. A piece of text that runs from right to left is reordered within its quotes; numbers of the server stand after a word of its own, so they keep their place.
- The list of look-alike quotes is made by hand from the characters known; a sign of a script not on it could resemble one. What follows an excerpt is only the word becomes or the number of characters left out.
- Letters that look alike, and composed and decomposed forms of one letter, cannot be told apart in the confirmation; the dry run holds them exactly.
- The refusal holds more document text than others. It is not logged, but reaches traces if an operator exports them (decision-15).
- Writing text can plant an instruction for later sessions: hence opt-in, destructive, confirmed with a visible excerpt, and the rule that document text is data.

## Alternatives

- Parameters on `update_document`: no tool slot, but a non-destructive annotation and five more parameters in every session; rejected.
- A default tool with a budget of 21, or a toolset of its own for one tool; rejected.
- `modified` as the change check: any metadata edit moves it, and reprocessing without barcodes does not; rejected.
- The whole text in the confirmation: too long for a dialog; hence the dry run.

## Enforcement

- `tests/services/test_text_edits.py`: replacement, uniqueness, the state, limits, the shown excerpt, the scope, the dry run, what is reported after the write; unconfirmed paths run on a fake that refuses writes. `tests/domain/test_errors.py`: the escaping. `tests/services/test_documents.py`: the state of a text.
- `tests/adapter/test_document_writes.py` and `tests/adapter/test_documents.py`: `version` and `content` in every PATCH, no retry, deleted versions. `tests/contract/test_document_versions.py` (reading) and `tests/contract/test_text_edits.py` (writing): the text of versions on the fake and on Paperless 3.5.1, with restricted users.
- `tests/tools/test_text_edits.py`: opt-in, annotations, description, structured output. `tests/tools/test_confirmation.py` and `tests/tools/test_confirmation_text.py`: both eras, hostile text only in the escaped excerpt. `tests/tools/test_registry.py`: `confirm` and the default budget.
- `tests/e2e/test_text_edits.py`: correction, search, reprocessing, change in between and a restricted user on Paperless 3.5.1.
