---
updated_date: '2026-11-25T09:21:13Z'
id: decision-87
title: >-
  Permissions of mail accounts and rules are set by id, and the confirmation
  says what a change grant on an account gives
date: '2026-11-25T09:20:50Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The rest of AC 6 of PLMCP-81: `owner` and `set_permissions` of mail accounts and rules. `set_permissions` covered documents and taxonomy objects (decision-19). Read in the 3.5.1 source; the live tests are `tests/contract/test_permissions.py` and `tests/e2e/test_mail_import.py`:

- `/api/mail_accounts/{id}/` and `/api/mail_rules/{id}/` answer the grants with `full_perms=true`, which the vendored schema does not say, and take owner and grants on PATCH like the other objects. Their lists have no `id__in`.
- Only the owner, anyone for an object without owner, and a superuser may change owner or grants; a change grant is not enough.
- A change grant on an account lets its holder call `POST mail_accounts/test/` with the id and any server: Paperless signs in there with the saved password at once and stores nothing. It also lets them point the account at another server and make rules for it, but not fetch at once, delete it, or see its other rules.
- A change grant on a rule lets its holder set its action, folder and filters, also to move or delete mail with the saved password of the account, without any right on that account. Documents belong to the owner of the rule (`assign_owner_from_rule`); without an owner they are visible to every user with the global permission to view documents.
- Paperless sets the grants before it checks the name of the rule against the new owner, without a transaction: a 400 can leave the grants set. The name of a taxonomy object is checked first.
- The names of rules are unique per owner only.

The owner approved decision 4a of the design of PLMCP-81 on 2026-11-24 ("Alle wie empfohlen", all as recommended, in the question "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?").
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-19; everything of it holds.

- `set_permissions` takes `mail_account` and `mail_rule`, by id only. Each is read by a request of its own.
- The confirmation says what a change grant on an account or a rule gives wherever the call lets somebody change it who may not now: a new grant to change, another owner, or none.
- After a 400 to owner and grants together the object is read again, for every kind, and the error says whether the grants were set, or that it is not known.
- Reading and changing mail objects ends 40 seconds after the call began (decision-50); what was changed until then is named.
- Nothing but owner and grants is sent, so the saved password or token stays; the mask of it is not read.
- The design expected that a change grant lets its holder fetch; it does not, and the text does not say so.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Whoever is given a change grant on an account can have its saved password. `update_mail_account` asks before it is sent elsewhere; Paperless itself does not.
- 100 objects are 100 reads; a rate limit ends the call before anything is written, and the 40 seconds can end it between two writes.
- A grant on an account shows none of its rules; each rule is shared on its own.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/adapter/test_permissions.py`, `tests/services/test_permissions.py`, `tests/tools/test_permissions.py`: requests, ids only, the note.
- `tests/contract/test_permissions.py` (fake and Paperless 3.5.1), `tests/e2e/test_mail.py`: owner, grantee, what a grant gives.
