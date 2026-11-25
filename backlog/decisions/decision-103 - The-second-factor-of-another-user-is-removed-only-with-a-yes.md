---
id: decision-103
title: The second factor of another user is removed only with a yes
date: '2026-11-25T15:46:12Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
On 2026-11-24 the owner approved the design of PLMCP-83 with all recommended options ("Alle wie empfohlen", all as recommended; the question is quoted in decision-99). Its decision 3: cover `POST /api/users/{id}/deactivate_totp/`, because the password, the stronger change, can be set already.

Probed on Paperless 3.5.1 (CI run 45638730209, job 139291151398, a temporary test of PLMCP-83.2): a superuser removes it (200); a user without one is a 404 (text/html), an unknown user a server error, anybody else a 403. `is_mfa_enabled` of the user follows.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `update_user(remove_second_factor=true)`, supplementing decision-26: it always needs a yes (a dialog or `confirm`), and the question names the user and that the password alone then signs in.
- Never for the account this server signs in with; where that account cannot be told, nothing is removed (fail closed, as decision-26).
- In a call of its own: together with other changes it could be half done.
- A user without a second factor is told so and nothing is sent. The user is read again afterwards; a factor that is still there is an error, and where the user cannot be read again the error says that the removal may have been applied. A yes is for the account as it was shown. The request is never repeated.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Removing a second factor weakens an account: whoever knows the password signs in.
- A superuser cannot remove the own second factor of the server account here; the web UI does it.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_admin_accounts.py` ("the second factor"), `tests/tools/test_admin.py` (both dialog eras), `tests/adapter/test_admin.py`, `tests/contract/test_admin.py` on the fake and Paperless 3.5.1, `tests/e2e/test_admin.py` (a TOTP made in the Django shell).
