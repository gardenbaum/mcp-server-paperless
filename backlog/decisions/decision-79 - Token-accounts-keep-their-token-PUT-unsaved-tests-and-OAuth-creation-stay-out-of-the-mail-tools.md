---
updated_date: '2026-11-24T22:55:55Z'
id: decision-79
title: >-
  Token accounts keep their token; PUT, unsaved tests and OAuth creation stay
  out of the mail tools
date: '2026-11-24T22:55:19Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
AC 4 and 6 of PLMCP-81 want every endpoint of `mail_accounts`, `mail_rules`, `processed_mail` and `oauth` covered, an omission only with the owner's approval, and testing an unsaved account, creating an OAuth account or writing its token only after a decision. Read in the 3.5.1 source:

- `is_token`, `account_type`, `expiration` and `password` of an account are writable, `refresh_token` is not in the API. An account made that way is dead when its access token expires, and `update_mail_account(password=...)` overwrote the access token of an OAuth account.
- The OAuth callback needs the `oauth_state` of the browser session. The account it makes has an empty username, which the user sets afterwards; `expiration` is that of the access token, which Paperless renews at its next use.
- The test of an unsaved account makes Paperless connect to any host and port. Saving the account and testing it reaches the same hosts.
- The view of `process` ignores its body. PATCH does all that PUT does.

On 2026-11-24 the owner approved decisions 1 to 12 of the design of PLMCP-81: "Alle wie empfohlen" (all as recommended), in the question "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?".
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-18.

- `update_mail_account` refuses a `password` for an account with `is_token`, before anything is sent; the adapter reads `is_token` again right before the PATCH. All other settings are changed as before. A confirmed move of such an account says that its access token goes there.
- The tools say that OAuth accounts are set up in the web UI and that only a saved account can be tested.
- Left out, owner-approved as quoted above (2026-11-24): `PUT` of accounts and rules; the fields of the ignored body of `process`; the OAuth callback; `account_type`, `is_token` and `expiration` when an account is created or changed; testing an unsaved account (`POST mail_accounts/test/` sends the stored account only); `owner` and `set_permissions` when an account is created and `set_permissions` when a rule is created (they are set afterwards with `set_permissions`, PLMCP-81.5).
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A token cannot be written through this server any more. An account whose refresh token is revoked is set up again in the web UI.
- Between the last read of `is_token` and the PATCH someone can turn the account into a token account; the window is one request.
- `create_mail_account` and then `test_mail_account` still makes Paperless connect to any host without a confirmation, as before.
- The exclusions are rows of `docs/api-coverage.toml` and name this decision.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/adapter/test_mail.py`, `tests/services/test_mail.py`, `tests/tools/test_mail.py`, `tests/contract/test_mail.py` (fake and Paperless 3.5.1): the refusal, the race, the other settings, the texts.
- `tests/quality/test_api_coverage.py`: a row needs the quote and the date from this file.
