---
id: PLMCP-29
title: 'Toolset mail: accounts, rules and processed mail'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-14T00:45:00Z'
labels: []
milestone: m-4
dependencies:
  - PLMCP-15
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 29000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Mail import is configured through accounts and rules. Credentials must never flow back to the client (design §6, §7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Mail accounts list, get, create, update, delete (confirm), test and process; passwords are write-only and never returned
- [ ] #2 Mail rules list, get, create, update and delete (confirm) with taxonomy by name
- [ ] #3 list_processed_mail returns compact rows
- [ ] #4 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [ ] #5 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Domain (domain/mail.py): MailAccount, MailRule, ProcessedMail rows, write values (password is a SecretStr, write-only), compact output models; MailReader and MailWriter ports in domain/ports.py.
2. Adapter (paperless/mail.py): row models without any password field, schema-guarded adapter tests (tests/adapter/test_mail.py) incl. 'no password in anything the adapter returns'; test endpoint with long timeout, 400/500 become a failed result; process sends nothing retried.
3. FakePaperless (tests/fakes/mail.py + paperless.py) and contract suite (tests/contract/test_mail.py) on fake and live 3.4.2.
4. Service (services/mail.py): taxonomy by name via TaxonomyResolver, confirmations via ConfirmationRequired.for_effect (delete account/rule, process, rules that delete/move mails, server change without new password, owner change), compact processed-mail rows.
5. Tools (tools/mail.py, 13 tools, toolset mail, opt-in), registry/annotations in tools/_shared.py, surface snapshot, server wiring, import-linter contract, README, E2E (tests/e2e/test_mail.py).
6. Tests red first at every level; assert a known password never appears in any tool result, error or log. Gates: just check, just mutation, flock just e2e.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live probe of 3.4.2 (E2E stack): (a) GET/POST/PATCH of an account return password as '*' x len(password) (leaks the length); a PATCH with an all-asterisk password keeps the stored one, so an all-asterisk real password would be silently ignored -> the service refuses such passwords. (b) POST /api/mail_accounts/test/ with id and a '*' password uses the stored password server-side; connection refused or DNS failure answers HTTP 500 (not 400), an IMAP login failure 400 'Unable to connect to server' (plain text), unknown id 403, a server that accepts TCP but never greets (Paperless' own port) hangs the single web worker for >90 s. (c) POST .../process/ answers {result: OK} at once (queued celery task), 404 for unknown id. (d) Rule defaults: action 3, max age 30, folder INBOX, assign_owner_from_rule true; action 2 and 5 need an action_parameter (400 non_field_errors); stale action_parameter stays when action changes; filter fields accept '' and null; invalid pk -> 400 'Invalid pk'. (e) processed_mail statuses SUCCESS/FAILED; rows can only be produced by a real mail fetch.

Progress: domain (domain/mail.py, MailReader/MailWriter ports), adapter (paperless/mail.py, 59 schema-guarded tests, all green), FakePaperless (tests/fakes/mail.py) and the contract suite (tests/contract/test_mail.py, 30 tests green on fake AND live 3.4.2), MailService (services/mail.py, 79 tests) are done; tools, registry, snapshot, wiring, README, E2E and gates follow. Not red-first: I wrote the domain models, the port, the adapter and the fake before their tests (adapter and contract tests were written right after and run green; they were not seen failing against missing code). The service tests were written before the service (red: ImportError), the tool tests will be too.

Decisions (recorded as decision-18, accepted): (1) AC#1 passwords: write-only. Row models drop Paperless' mask ('*' per character, leaks length); MailAccount has no password field; password enters as SecretStr (writeOnly/format password in the schema), unwrapped only in the adapter body. A password sent as a JSON number is taken as text because pydantic quotes rejected input in its error and FastMCP does not mask it (found by a test; lists/objects/bools are still quoted back to the caller, documented in decision-18). Blank and asterisk-only passwords are refused (Paperless silently ignores an asterisk-only PATCH). Tests assert a known password and any run of asterisks appear in no tool result, error or log (adapter, service, tool, E2E); registry tests forbid credential-like output fields and require credential inputs to be writeOnly (allowlist: remove_document_password, a PDF password). OAuth tokens are never in the API; the tools show only account type and token expiry. (2) AC#1 test/process: test_mail_account(account_id) uses the saved password server-side (id + '*' placeholder), no confirm (changes nothing), open-world, not read-only; failed login/refused connection (HTTP 500 on 3.4.2)/silent server (45 s own timeout, nothing retried) are results with success=false. The E2E stack has no IMAP server and compose.yaml is unchanged on purpose: live tests cover the refused-connection path; login success, login failure and silence are covered by the fake, service and adapter tests. process_mail_account needs confirm (destructive+open-world): its refusal lists what each enabled rule does to the mail on the server; result only says queued. (3) AC#2 rules confirm: create/update need confirm when the resulting rule is enabled and deletes or moves mail, for updates only if enabled/account/folder/filters/age/action/parameter change; mark_read/flag/tag need none; owner change needs confirm (decision-12); delete rule/account need confirm. update_mail_account needs confirm when server/port/security change without a new password (saved password would go to the new host) or encryption is turned off. Taxonomy by name via TaxonomyResolver; assign_correspondent implies source 'selected'; changing action drops a stale action_parameter. (4) AC#3: list_processed_mail returns compact rows (no uid/owner, error cut to one 200-char line), server-side paging, newest first. Deliberate gate changes: schema_guard WRONG_RESPONSE_SCHEMAS gets POST /api/mail_accounts/test/ 400 and 403 (text/html bodies the schema calls JSON); surface snapshot regenerated (13 new tools, default profile unchanged at 18). Not included (out of scope): processed_mail bulk_delete, OAuth account creation. Mutation: scoped run on services.mail and paperless.mail: 1515 mutants, 169 survived (88.8 % killed); no equivalents added; the score script's overall 11.3 % counts the 10k mutants outside the selection as not checked.
<!-- SECTION:NOTES:END -->
