---
id: decision-107
title: Without an answer a record that is not found is deleted only for a superuser
date: '2026-11-25T16:09:32Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-83 says that a record is deleted if it is not there any more when it is read again. A record that became hidden meanwhile (its owner was set to somebody else) is not found either, so it could be called deleted.

The owner decided on 2026-11-26, "Alles wie von dir empfohlen." (everything as you recommend), that a record that cannot be read after a deletion counts as deleted only if that can be shown, otherwise as unknown.

Read in the source of Paperless 3.5.1 (`ProcessedMailViewSet`): `bulk_delete` looks the ids up without a filter for what the user may see, answers 403 if one of them may not be deleted, and deletes all others in one step before it answers `OK`. The list and a single record go through the filter for what the user may see; a superuser sees every record. So the list says no more than the single record does.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supersedes one point of decision-83: "deleted only if it is not there any more". Everything else of it stands.

A record that is not found after the deletion counts as deleted if

- Paperless answered `OK`, because then no record of these ids was left, hidden or not; or
- there was no such answer, and the user was a superuser before the deletion and still is after the records were read again.

Otherwise it is unknown, and the text says that it is not visible any more, so deleted or hidden. An answer that is not Paperless's `OK` (a page of a proxy) is no answer. Who is signed in is asked only without an answer, not after a rate limit and not 40 seconds after the call began.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A user who is no superuser and whose answer was lost is told "unknown" also where the record is gone; `list_processed_mail` cannot tell more.
- One more request, only without an answer.
- Somebody who loses and regains the rights of a superuser while the records are read is not noticed.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives
<!-- SECTION:ALTERNATIVES:BEGIN -->

- Unknown also after `OK`: the source shows that nothing is left.
- Looking into the list of the records: the same filter.
- Sending the deletion again: it is never repeated.

<!-- SECTION:ALTERNATIVES:END -->
## Enforcement

- `tests/services/test_processed_mail.py`, `tests/tools/test_mail.py`: hidden, gone, superuser before and after, rate limit, time.
- `tests/adapter/test_mail.py`: what confirms a deletion.
