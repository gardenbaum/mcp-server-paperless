---
id: decision-18
title: Mail credentials are write-only; mail-server actions need confirm
date: '2026-11-14T00:27:00Z'
status: accepted
---
## Context

The mail toolset (PLMCP-29) manages the IMAP accounts and rules that Paperless fetches documents with. An account holds a secret (a password, or an OAuth access token set up in the web UI) and a rule can delete or move mail on the mail server once it has imported its documents. Probed on Paperless 3.4.2:

- Paperless masks the password in every answer with `*` repeated `max(10, length)` times, so even the mask gives away the length of a long password. A PATCH with a password of asterisks only is ignored ("keep the saved password"), so a real password of that kind would be dropped silently. The OAuth refresh token is not in the API at all.
- `POST /api/mail_accounts/test/` with an account id and a password of `*` logs in with the saved password. Refused connections and unknown hosts answer 500, a failed login 400, and a server that accepts the connection but never answers holds the single web worker for minutes. `POST /api/mail_accounts/{id}/process/` queues a fetch at once and always runs the rules, whose actions mark, flag, tag, move or delete mail on the server.
- Pointing an account at another host keeps the saved password, which Paperless then sends to the new host at the next fetch.

An LLM with these tools could leak a password through a result, an error or a log line, send the saved password to an attacker's server, or delete mail it cannot bring back. Decision-6 reserves `confirm` for destructive tools, decision-15 makes the refusal an error and decision-17 extended it to publishing; this decision applies the same mechanism to mail.

## Decision

- Credentials are write-only. A password enters as a `SecretStr` (tool parameter with `writeOnly` and format `password`), is unwrapped in one place (the adapter's request body), and no domain model, tool output, error, confirmation text or log line has a password, an OAuth token, a refresh token or the mask of one. A mail account row has no password field at all; the adapter's row model does not declare it, so the mask is dropped at the parse. The password parameter has a validator that never raises: text becomes a `SecretStr`, anything else (number, boolean, null, list, object) becomes a sentinel that the service refuses with "The password must be text", because pydantic quotes a rejected input in its error (`input_value=`), FastMCP passes that error to the client and records it in traces. The schema is pinned to a write-only string. A blank password and one made of asterisks only are refused without echoing it.
- `delete_mail_account` and `delete_mail_rule` are destructive and need `confirm`; the refusal names the account, its rules and that their processed-mail records go too.
- `process_mail_account` is destructive and open-world and needs `confirm`: the refusal lists what each enabled rule does to the mail on the server, and says a deleted mail cannot be put back and, for a caller who is not a superuser, that rules of other users on the account that the caller cannot see run too. `test_mail_account` changes nothing and needs no `confirm`; it is open-world, not read-only, and gives up on a silent server after 45 seconds. A failed connection is a result (`success: false`), not an error.
- `create_mail_rule` and `update_mail_rule` need `confirm` when the resulting rule is enabled and its action is delete or move, and for an update only if it changes what the rule reaches (enabled, account, folder, filters, age, action, action parameter, consumption scope, attachment type). Changing the order of an enabled rule, or switching off its `stop_processing`, also needs it while another enabled rule of the same account deletes or moves mail (conservative: that rule may now run for mails this rule imported from). Mark read, flag and tag need none; renaming or reordering a deleting rule needs none; a disabled deleting rule needs none until it is switched on.
- `update_mail_account` needs `confirm` when it changes the server, port or security without giving a new password in the same call (the saved password would go to the new target), and when it turns encryption off.
- Handing a rule to another owner or to nobody needs `confirm` (decision-12), and so does creating a rule for an owner other than the caller (or when the caller cannot be identified). Rule taxonomy (tags, correspondent, document type, owner) is given by name or id and resolved by the `TaxonomyResolver` (decision-5).
- OAuth accounts (Gmail, Outlook) are created in the Paperless web UI; the tools neither create nor show their tokens, only the account type and the token expiry.
- This supplements decisions 6, 12, 15 and 17; it changes none of their rules.

## Consequences

- Fetching mail, deleting an account or a rule and creating a deleting rule take two calls by design.
- A client cannot read a password back, and neither can the operator through this server. Losing a password means setting a new one.
- Changing the host of an account needs a new password or a confirmed redirect, so the model cannot quietly send the saved password elsewhere.
- Paperless' own limits stay: `process` is a POST and needs the add permission, `test` needs the change permission on the account.
- A password that is not text is refused without quoting it, so no error, log or trace carries it.
- Accepted gap: a rule's other settings (for example `pdf_layout`, the title or correspondent source) change what is imported but not which mails are acted on, and need no confirm.

## Enforcement

- Registry tests (tests/tools/test_registry.py): no tool output schema has a credential-like field, every credential-like input is `writeOnly`, and `update_mail_account`, `create_mail_rule` and `update_mail_rule` carry an optional `confirm` that defaults to false, each with a self-test over a synthetic offending tool; the existing destructive-tool check covers the deletes and `process_mail_account`.
- Adapter tests (tests/adapter/test_mail.py): rows drop the masked password, asterisk passwords are refused, a rejected account never quotes the value in the error or the log, the test and process requests carry the `*` placeholder and never the password.
- Service tests (tests/services/test_mail.py): every refusal text, nothing written without `confirm` (the fake is created without `allow_writes`), a known password is in no result, message or log line.
- Tool tests (tests/tools/test_mail.py): a known password and any run of asterisks appear in no tool result, error or log; a numeric password is not echoed. Contract tests on the fake and live 3.4.2 (tests/contract/test_mail.py) and E2E (tests/e2e/test_mail.py) run the flows through the real adapter.
