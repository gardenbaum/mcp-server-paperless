---
id: PLMCP-98
title: Ask the user to confirm destructive actions in a dialog where the client can
status: In Progress
assignee: []
created_date: '2026-11-23T18:44:53Z'
updated_date: '2026-11-23T20:43:08Z'
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
<!-- SECTION:NOTES:END -->
