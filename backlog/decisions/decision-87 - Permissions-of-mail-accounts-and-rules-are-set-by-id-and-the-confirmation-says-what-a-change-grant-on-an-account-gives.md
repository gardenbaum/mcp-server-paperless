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
The rest of AC 6 of PLMCP-81: `owner` and `set_permissions` of mail accounts and rules. `set_permissions` covered documents and taxonomy objects (decision-19). Read in the 3.5.1 source and checked live (`tests/contract/test_permissions.py`):

- `/api/mail_accounts/{id}/` and `/api/mail_rules/{id}/` answer the grants with `full_perms=true`, which the vendored schema does not say, and take owner and grants on PATCH like the other objects. Their lists have no `id__in`.
- Only the owner, anyone for an object without owner, and a superuser may change owner or grants; a change grant is not enough.
- A change grant on an account lets its holder point it at another server (the saved password goes there at the next fetch) and make rules for it, but not fetch at once, delete it, or see its other rules.
- The names of rules are unique per owner only.

The owner approved decision 4a of the design of PLMCP-81 on 2026-11-24 ("Alle wie empfohlen", all as recommended, in the question "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?").
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-19; everything of it holds.

- `set_permissions` takes `mail_account` and `mail_rule`, by id only. Each is read by a request of its own.
- The confirmation for an account says what a change grant gives wherever the call lets somebody change it who may not now: a new grant to change, another owner, or none.
- Nothing but owner and grants is sent, so the saved password or token stays; the mask of it is not read.
- The design expected that a change grant lets its holder fetch; it does not, and the text does not say so.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Whoever is given a change grant on an account can send its saved password to a server of theirs. `update_mail_account` asks before that here, the web UI does not.
- 100 objects are 100 reads; a rate limit ends the call before anything is written.
- A grant on an account shows none of its rules; each rule is shared on its own.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/adapter/test_permissions.py`, `tests/services/test_permissions.py`, `tests/tools/test_permissions.py`: requests, ids only, the note.
- `tests/contract/test_permissions.py` (fake and Paperless 3.5.1), `tests/e2e/test_mail.py`: owner, grantee, what a grant gives.
