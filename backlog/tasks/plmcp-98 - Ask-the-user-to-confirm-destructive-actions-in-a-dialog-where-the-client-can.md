---
id: PLMCP-98
title: Ask the user to confirm destructive actions in a dialog where the client can
status: In Progress
assignee: []
created_date: '2026-11-23T18:44:53Z'
updated_date: '2026-11-23T21:20:17Z'
labels:
  - tools
  - safety
dependencies: []
references:
  - https://gofastmcp.com/docs/servers/elicitation.md
documentation:
  - docs/vendor/fastmcp/docs/servers/elicitation.mdx
priority: high
type: feature
ordinal: 74000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every confirmation today is an error plus a second call with confirm=true (decision-6, decision-15). The assistant, not the user, sets that flag, so the server cannot tell whether the user agreed. Decision-6 gave one reason for not using elicitation: it is unavailable in the sessionless era. That no longer holds with FastMCP 4: ctx.elicit() serves handshake connections (up to 2025-11-25) and a returned InputRequiredResult (guard pattern) serves 2026-07-28. Each raises on the other era and a client without an elicitation handler can use neither, so the confirm=true flow has to stay as the fallback. Owner request 2026-11-23: use elicitation as well as possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A new decision records the choice and supersedes the parts of decision-6 and decision-15 that rule elicitation out; doc-1 5.5, doc-2, docs/fastmcp-reading-guide.md, domain/errors.py, the confirm parameter description, README.md and docs/configuration.md agree with it
- [ ] #2 A call without confirm on a client that declares form elicitation shows the user the same effect text as the refusal, and runs only after the user accepts, on both protocol eras
- [ ] #3 Decline, cancel and a malformed answer change nothing and return an error that tells the assistant not to retry on its own; a timeout, a disconnect, or an expired or invalid state changes nothing
- [ ] #4 Clients that do not declare form elicitation get exactly the present refusal and confirm=true flow, except for two message changes the owner approved on 2026-11-23: the owner-change refusal of update_document names the document and the new owner, and messages lose control, bidirectional and tag characters
- [ ] #5 An acceptance applies only to the call and the effect that was shown: with changed arguments it changes nothing, and a target whose effect changed in between asks again and changes nothing
- [ ] #6 On 2026-07-28, an answer without valid server-issued state for this effect, or with state already used, changes nothing
- [ ] #7 One mechanism covers every tool with a confirm parameter, guarded by a registry test with a self-test; no tool is added to the default profile
- [ ] #8 Error handling, logging, redaction, output schemas and read-only mode are verified on rounds that ask; the dialog text and answers are not logged and never hold a secret
- [ ] #9 Unit and E2E tests drive accept, decline, cancel and no-handler on a handshake and a 2026-07-28 connection, over stdio and HTTP, including a call that waits longer than the usual request timeouts
- [ ] #10 The behaviour is tried in Claude Code and one further real client, and the documentation says which clients were seen to show the dialog
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
1. Domain: ConfirmationRequired.require(effect, scope=...) raises unless a ContextVar holds the accepted fingerprint (message and scope) of exactly this refusal; replace every raise site; scope where the text is capped or names are resolved; strip control, bidi and tag characters; rewrite the owner-change refusal. AST guard with a self-test.
2. tools/confirmation.py: form capability, handshake dialog (10 min, progress every 30 s, disconnect check, at most 3 dialogs), 2026-07-28 rounds (own sealed state with nonce, fingerprint and expiry; nonce burnt on any answer). Wired in the boundary; Dialogs held by create_server; SDK state TTL longer than ours.
3. CallLogging outcome asked; refused answers logged as NotConfirmed; logging, redaction, spans, output schemas and read-only mode tested on rounds that ask.
4. Tests: every confirm tool on both eras; raw sessions for forged, replayed, expired, other arguments, process and principal; real HTTP for disconnect, other principal, idle expiry; E2E over the two entrypoints including a wait over 60 s; proxy run.
5. New decision superseding parts of 6 and 15; doc-1, doc-2, reading guide, errors.py, confirm description, README, configuration, http docs.
6. Steps for Claude Code and VS Code (criterion 10 stays open).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner approval 2026-11-23 of the design (binding parts for PLMCP-98): variant B (ConfirmationRequired.require, the read that is checked is the one written); wait 10 minutes with progress notifications; a declaring client that answers -32601 gets the present refusal; a declaring client that always cancels keeps only confirm=true on the first call; VS Code is the second client of criterion 10; single process only, documented; the owner-change refusal is rewritten and control characters are stripped.
Criterion 4 was reworded for exactly these two message changes (allowed by the same approval); nothing else in it changed.

Implementation and evidence (2026-11-23):
Decision-44 (42 is on the branch of PLMCP-74, 43 is on main). One call site each uses ConfirmationRequired.require; 2026-07-28 reads the target once in the answering round (the state's fingerprint is checked by the write's own read), which is one pass less than the design says.
Deliberate changes: test_surface.ambr (43 confirm descriptions, 2 of update_document), test_prompts.ambr with the plugin copy, test_edit_messages.ambr (owner text), docs/tools.md regenerated; test_a_refusal_carries_text_only now allows the fingerprint (a hash). No gate, threshold or contract changed. Default profile unchanged.
Limits of the spikes, now shown: variant B over HTTP and stdio on both eras (E2E, 17 tests on 3.5.1); an acceptance after the dropped connection on a handshake, and a connection dropped in the answering round on 2026-07-28 (tests/test_confirmation_http.py); other bearer token on 2026-07-28 and in the same handshake session; idle expiry with a dialog open; scope (25 cases); malformed answers by raw messages on both eras; 65 s open dialog over stdio and HTTP on both eras. Run by hand: nginx with its default 60 s read timeout, 75 s dialog, both eras, progress at 30 and 60 s through it; OpenTelemetry SDK, both eras, accept and decline: no title or effect text in any span; 105 open dialogs at once and another call still answered in 1.1 s, then all 105 accepted (the HTTP pool to Paperless holds 100).
Found on the way: the cancelled round on 2026-07-28 uses its state up, so the same answer on a new connection asks again.

Steps for criterion 10 (nothing below changes a client configuration; run in the worktree):
1. COMPOSE_PROJECT_NAME=plmcp98-client PAPERLESS_E2E_PORT=8031 docker compose -f tests/e2e/compose.yaml up -d; wait until healthy. T=$(curl -s -d username=admin -d password=e2e-admin-password http://127.0.0.1:8031/api/token/ | jq -r .token); curl -s -H "Authorization: Token $T" -d name=dialog-test http://127.0.0.1:8031/api/tags/
2. Claude Code, stdio: claude --strict-mcp-config --mcp-config '{"mcpServers":{"paperless":{"command":"uv","args":["run","--directory","'$PWD'","mcp-server-paperless"],"env":{"PAPERLESS_URL":"http://127.0.0.1:8031","PAPERLESS_TOKEN":"'$T'"}}}}'
3. Ask: Delete the tag dialog-test with delete_taxonomy_item, without confirm. Expect a dialog that begins "Nothing was changed. Confirming would delete tag 'dialog-test'". (a) Decline: the tag is still there and the assistant does not call again by itself. (b) Again, close it with Esc: same. (c) Again, wait 6 minutes, then accept: the tag is gone (checks progress against the client's 5 minute limit). Note the version (claude --version) and whether step (c) was cut off. Create the tag again between the runs.
4. Claude Code, HTTP: in another terminal PAPERLESS_URL=http://127.0.0.1:8031 PAPERLESS_TOKEN=$T PAPERLESS_MCP_HTTP_AUTH_TOKENS=test-token-not-a-secret uv run mcp-server-paperless-http; then claude --strict-mcp-config --mcp-config '{"mcpServers":{"paperless":{"type":"http","url":"http://127.0.0.1:8000/mcp","headers":{"Authorization":"Bearer test-token-not-a-secret"}}}}' and step 3. Also claude -p with the same options (no person): expect an error, the tag stays.
5. VS Code with Copilot (agent mode): an empty folder with .vscode/mcp.json {"servers":{"paperless":{"type":"stdio","command":"uv","args":["run","--directory","<worktree>","mcp-server-paperless"],"env":{"PAPERLESS_URL":"http://127.0.0.1:8031","PAPERLESS_TOKEN":"<T>"}}}}; open it, start the server from the file, then step 3 in the chat. Note the VS Code version.
6. COMPOSE_PROJECT_NAME=plmcp98-client docker compose -f tests/e2e/compose.yaml down --volumes; delete the folder. Then write the clients seen into docs/configuration.md (Confirmation) and tick criterion 10.
<!-- SECTION:NOTES:END -->
