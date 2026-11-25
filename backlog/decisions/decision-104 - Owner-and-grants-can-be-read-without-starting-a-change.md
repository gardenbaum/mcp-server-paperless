---
id: decision-104
title: Owner and grants can be read without starting a change
date: '2026-11-25T15:46:13Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Owner and grants could be seen only by starting a change with `set_permissions`. On 2026-11-24 the owner approved the design of PLMCP-83 with all recommended options ("Alle wie empfohlen", all as recommended; the question is quoted in decision-99). Its decision 8: `get_permissions`; with it the `full_perms` of one object stays an exclusion (decision-99).

Paperless shows the grants of an object to whoever sees the object (decision-91, probed on 3.5.1 in PLMCP-88.1; contract tests of PLMCP-28).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `get_permissions(kind, items)`, read-only, 1 to 100 objects of the kinds `set_permissions` takes, chosen the same way and read by the same reader. Supplements decision-19, which holds for every change.
- Users and groups are shown by name, as `#id` where the user may not list them.
- Ids that do not exist or are hidden are named in `missing`, for every kind; the others are read. An unknown name is an error; names are looked up in a list read anew, ids are read as they are, so a new object is found.
- Saved views, mail accounts and rules are read one by one for at most 40 seconds; the names of users and groups are read first and kept, so nothing is asked for after the time is over. An answer holds at most 100,000 characters, but always one object. `left` names what was not read or not shown.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The superusers and everyone with the global permission on an object without an owner are not in the grants; the tool description says so.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_permissions.py` ("reading"), `tests/tools/test_permissions.py`, `tests/e2e/test_permissions.py`; the reader's contract tests in `tests/contract/test_permissions.py`.
