---
id: decision-53
title: Operators can require that a confirmation comes from an accepted dialog
date: '2026-11-24T07:37:34Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-44 lets `confirm=true` stand in for the user's answer, because clients without form dialogs have no other way. So the assistant can skip the dialog, for example after a prompt injection in a document. An operator whose clients show dialogs had no way to make the answer binding (PLMCP-99).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `PAPERLESS_MCP_REQUIRE_DIALOG` (off by default, on both transports) lets only an accepted dialog confirm. Off, nothing differs from decision-44.
- On, the tool boundary passes `confirm=false` to every tool with that parameter, whatever the caller sent. So every confirmation reaches `ConfirmationRequired.require`, and only the acceptance of exactly that text and scope passes it. There is one place for all tools, present and future; no service knows the setting.
- On, it fails closed: a client that does not declare form elicitation, a connection on which the server cannot ask, a client that answers that it has no such method, a dialog that cannot be shown, is declined, closed, misunderstood or not answered in time, a dropped connection and a target that changes under all three dialogs change nothing. The error is `NotConfirmed`: the reason, the setting, that `confirm=true` does not replace the dialog, the way out where there was nobody to ask (a client that shows dialogs, or the operator switches the setting off; never after a user's no), and not to call again on one's own. The way past it that decision-44 gives a client without dialogs is not offered.
- Tool schemas, descriptions, prompts, the server instructions and the hints in results (the dry runs of the bulk tools, the duplicates of a batch upload) do not depend on the setting.
- A `confirm` that is passed where nothing needs one is ignored.
- The setting is in the configuration reference and in `server.json`, whose version the release writes from the tag, so a published listing always describes a release that knows it. The Claude Code plugin gets the option, and with it the README table (a test keeps the two equal), together with the first pin that knows the setting. It is an operator's choice, like the toolsets (decision-41): no tool changes it.
- Supplements decision-44, which stays as it is for the default.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- It is no proof of a human: a client, a hook or a script can accept dialogs automatically.
- With a client that cannot show dialogs, or that always cancels them, destructive tools cannot be used at all.
- Descriptions, hints and the prompt `cleanup_taxonomy` still say that `confirm=true` goes ahead at once; with the setting on an assistant that does so causes a dialog or an error, never a change.
- A tool that took its confirmation from anything but a top-level `confirm` would escape the setting; a registry-wide test fails for one whose schema names a confirmation elsewhere, and for a new tool without a case.
- Several server processes behind one address stay unsupported for dialogs: an answer that reaches another process changes nothing.
- Releases before the setting ignore the variable: whoever sets it there is not protected. The plugin pins such a release (0.3.2), so it must not offer the option yet; the reference names the last release without it.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

- Switching only the two fallbacks to the refusal, as decision-44 expected: `confirm=true` on the first call would still skip the dialog.
- Removing `confirm` from the schemas, or texts per setting: every snapshot and budget twice, and a client that sends the flag anyway would get a schema error instead of a dialog; not now.
- Repeating the effect in the error: it would read like something to confirm, and there is nothing to confirm it with.

## Enforcement

- `tests/tools/test_require_dialog.py`: every tool with `confirm`, over both transports and both eras, with and without `confirm=true`, found in the registry; every way a dialog ends; code passes on only the caller's own `confirm` (AST check over every module).
- `tests/test_confirmation_http.py`: declared capabilities, failing dialogs and a stateless server on the wire. `tests/e2e/test_confirmation.py`: both entrypoints and eras against Paperless.
