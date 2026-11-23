---
id: decision-44
title: >-
  Destructive actions are confirmed in a dialog where the client can show one,
  confirm=true stays the fallback
date: '2026-11-23T20:43:45Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-6 and decision-15 confirm destructive calls with an error and a second call that carries `confirm=true`, and rule elicitation out because it is not available on every protocol era. The assistant sets that flag, not the user, so the server cannot tell whether the user agreed. With FastMCP 4 both eras can ask: `elicitation/create` on handshake connections (up to 2025-11-25) and a returned `InputRequiredResult` on 2026-07-28. Each works on its own era only, and a client without a form dialog can use neither. The owner approved the design on 2026-11-23 (PLMCP-98).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- A call that needs a confirmation it does not have shows the user the refusal's text in a dialog with one required yes/no field, if the client declares form elicitation (`form`, or neither `form` nor `url`; a client that declares only `url` is not asked). The call runs only after the user accepts with exactly `{"confirm": true}`.
- Everything else keeps decision-15: a client that does not declare it, or declares it and answers that it has no such method, gets the refusal and the `confirm=true` flow. `confirm=true` on the first call runs without a dialog.
- Decline, cancel, a malformed answer, a timeout (10 minutes, with progress notifications every 30 seconds) and an answer after the caller's HTTP connection dropped change nothing and return an error that says not to call again on one's own. Every answered dialog ends the question; nothing is retried.
- One mechanism for every tool with a `confirm` parameter: services call `ConfirmationRequired.require(effect, scope=...)`, which refuses unless the running call carries the user's acceptance of exactly this text and `scope`. The boundary asks, then runs the tool again with that acceptance, so the read that is checked is the read that is written. `scope` holds what the text does not tell apart (the ids behind "and 5 more", the object behind a name). A refusal with another text or scope asks again: at most three dialogs in one handshake call, then the refusal.
- On 2026-07-28 the server's own state (a nonce, the fingerprint of text and scope, an expiry) travels sealed by the SDK, which binds it to method, tool, arguments, principal, server and process. An answer without it is not read. The nonce is used up by any answer.
- The fingerprint is a hash: the error still carries text only, and neither dialog texts nor answers are logged. A call that asks is logged with outcome `asked`, a refused one as `NotConfirmed`.
- Messages drop control, bidirectional and tag characters and stay on one line, because a document title is someone else's text inside the dialog. The refusal of an owner change names the document and the new owner.
- State and sessions live in one process: several replicas behind one address are not supported for dialogs (an answer that reaches another process changes nothing).
- Supersedes "Elicitation is not used" in decision-6 and its reason in the Context, "elicitation is not available on every protocol era" as the reason in decision-15, and the two-calls-by-design consequence of both for clients with a dialog. Everything else in them stands.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A dialog call reads its target twice, as the refusal and the second call did.
- A client that declares forms but always cancels (some do when nobody is present) has only `confirm=true` on the first call.
- The server still cannot force the dialog: an assistant can pass `confirm=true` itself. Requiring the user's answer is PLMCP-99, which needs one switch at the two places that fall back to the refusal.
- While a handshake call waits it holds one task and its response stream, but no Paperless connection or lock. On 2026-07-28 nothing waits.
- The check for a dropped connection would wrongly refuse a resumed stream; the server has no event store, and adding one has to revisit it. A connection that died without the server's TCP stack noticing still counts as open.
- A hook or a client that answers without a person is indistinguishable from a person.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Run the call with `confirm=True` after comparing a fresh refusal with the accepted one (variant A): services untouched, but the checked read is not the written one and a second confirmation in a call would be skipped; rejected by the owner.
- FastMCP middleware: it sees only the mapped error, not the effect; rejected.
- A shared key for several replicas: needs a shared store of used nonces too; not now.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/quality/test_confirmation_sites.py`: only the domain builds the refusal (with a self-test). `tests/domain/test_errors.py`: `require`, the fingerprint, hidden characters. `tests/services/test_confirmation_scope.py`: an acceptance does not cover other targets behind the same text.
- `tests/tools/test_confirmation.py`: every tool with `confirm` asks and runs on both eras, and decline, cancel, malformed, forged, replayed, expired and foreign answers change nothing. `tests/tools/test_registry.py` keeps every tool behind the boundary.
- `tests/test_confirmation_http.py`: dropped connections, other principals, idle expiry and progress over HTTP. `tests/e2e/test_confirmation.py`: both entrypoints and eras against Paperless.
