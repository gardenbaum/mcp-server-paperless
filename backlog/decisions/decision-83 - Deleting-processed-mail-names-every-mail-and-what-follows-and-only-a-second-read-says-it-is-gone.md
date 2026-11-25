---
updated_date: '2026-11-25T07:26:56Z'
id: decision-83
title: >-
  Deleting processed mail names every mail and what follows, and only a second
  read says it is gone
date: '2026-11-25T07:26:34Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
AC 2 of PLMCP-81. A record of processed mail keeps Paperless from fetching a mail again. Proven on Paperless 3.5.1 (PLMCP-81.2, `tests/e2e/test_mail_import.py`, `tests/contract/test_mail.py`):

- `bulk_delete` skips ids that do not exist and lists every id it was sent as deleted. One record the caller may not delete refuses all of them before any is deleted (403). It needs `delete_processedmail` and no view permission; grants to delete cannot be given through the API.
- A mail is fetched again if it matches the rule, is not yet marked by its action, and no record of the rule, folder and uid is left. A mail that failed is taken whole again, and then the rule acts, also by deleting it. A rule with `eml_and_attachments` writes two records, and either keeps the mail out.

The owner approved decisions 9a and 11a of the design of PLMCP-81 on 2026-11-24 ("Alle wie empfohlen", all as recommended, in the question "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?"): 100 records a call, every mail named; an id that is missing refuses the call.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-6, decision-18 and decision-44.

- `delete_processed_mail` takes 1 to 100 ids, each once, and is confirmed always. The confirmation names every mail (id, subject, when it arrived, rule, status, other records that keep it out and stay) and says once per kind of outcome what follows; `dry_run` shows the same text.
- It is for the records, the rules, the mailboxes of their accounts and the records that stay as they were read: a change of one asks again.
- Every id is read before anything is sent. One that does not exist or is hidden refuses the call, and each is named. A missing permission and a record of another user are told beforehand where the caller is known; otherwise Paperless's 403 says that nothing was deleted.
- One request, never repeated. Then each record is read again: deleted only if it is not there any more, still there, or unknown. A lost answer is followed by the same reading.
- One request at a time, and nothing is started 40 seconds after the call began (decision-50): before the deletion the call ends and nothing was deleted, afterwards what was not read is unknown.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The fetch that follows happens without another question and can delete mail on the server: the confirmation says so.
- The tool needs `view_processedmail` as well, though Paperless does not.
- Between the last read and the deletion, and until the next fetch, the rule or the mail can change. Only the newest 1,000 records of a rule are searched for others of the same mail, and that is said. Others are found by folder and uid: a record of a folder that was made anew since, or one the user may not see, is not told apart or not counted; the texts say so.
- A grant to delete set in the Django admin does not count: the record of another user is refused beforehand.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives
<!-- SECTION:ALTERNATIVES:BEGIN -->

- Believing the answer: it names ids that were never there.
- Deleting the records that exist when one is missing: the caller has the wrong ids, and Paperless would hide it.
- 25 records a call: four confirmations for what one shows.

<!-- SECTION:ALTERNATIVES:END -->
## Enforcement

- `tests/adapter/test_mail.py`, `tests/services/test_processed_mail.py`, `tests/tools/test_mail.py`: request, texts, scope, refusals, outcomes, time.
- `tests/contract/test_mail.py` (fake and Paperless 3.5.1): skipped ids, a foreign record refuses an older one of the caller's, the permission.
- `tests/e2e/test_mail_import.py`: deleted, fetched again, and the rule then deletes the mail.
