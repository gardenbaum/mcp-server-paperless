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
- A mail is fetched again if it matches the rule, is not yet marked by its action, and no record of the rule, folder and uid is left. A mail that failed is taken again, and the rule acts once what it queued is consumed, also by deleting it. A rule with `eml_and_attachments` queues the mail itself and its attachments apart, each with the action behind it: the rule acts as soon as one of the two halves is consumed, even if the other fails. It can write two records, and either keeps the mail out. The mail is queued before its attachments are looked at: a mail without any has SUCCESS beside PROCESSED_WO_CONSUMPTION, or only the latter if its import failed, and a SUCCESS can hide that the other half failed (the error is recorded only if there is no record yet).

The owner approved decisions 9a and 11a of the design of PLMCP-81 on 2026-11-24 ("Alle wie empfohlen", all as recommended, in the question "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?"): 100 records a call, every mail named; an id that is missing refuses the call.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-6, decision-18 and decision-44.

- `delete_processed_mail` takes 1 to 100 ids, each once, and is confirmed always. The confirmation names every mail (id, subject, when it arrived, rule, status, other records that keep it out and stay for now) and says once per kind of outcome what follows; `dry_run` shows the same text.
- A confirmation of more than 100,000 characters (the whole text with its hint, as for `run_workflow`) is refused, also with `confirm=true` and in a dialog, and never cut. `dry_run` still shows the whole text and says first that the call would be refused, with the sizes.
- It is for the records, the rules, the mailboxes of their accounts and the records that stay as they were read: a change of one asks again.
- Every id is read before anything is sent. One that does not exist or is hidden refuses the call, and each is named. A missing permission and a record of another user are told beforehand where the caller is known; otherwise Paperless's 403 says that nothing was deleted.
- One request, never repeated. Then each record is read again: deleted only if it is not there any more, still there, or unknown. A lost answer is followed by the same reading.
- For a rule the user may not see, the text says that the mail may be imported again and then deleted or moved.
- One request at a time, and nothing is started 40 seconds after the call began (decision-50): before the deletion the call ends and nothing was deleted, afterwards what was not read is unknown.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- For 100 records measured 11,341 characters (one rule), 130,252 (100 rules that move to long folders, nothing hostile), 170,052 (the same with staying records and other folders) and 305,452 (every text made of escapes): calls like the last three are refused and need fewer records. The owner approved the bound on 2026-11-25 ("Ja, ablehnen (Recommended)", yes, refuse, in a question dialog).
- The fetch that follows happens without another question and can delete mail on the server: the confirmation says so.
- The tool needs `view_processedmail` as well, though Paperless does not.
- Between the last read and the deletion, and until the next fetch, the rule or the mail can change. Only the newest 1,000 records of a rule are searched for others of the same mail, and that is said. Others are found by folder and uid. The API does not show the UIDVALIDITY of a record: one without it keeps out any mail with that uid, one with an older one none (any, if the server names no UIDVALIDITY), and records the user may not see are not counted; every text says so. A record of `INBOX` keeps nothing out of a rule that reads `inbox`, because Paperless compares the folder exactly while the server does not; the texts treat the two as one folder, so they speak of such a record as if it kept its mail out (the records of the folder as the rule writes it are the ones named). What an earlier rule of the account does in the same fetch is not worked out.
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
