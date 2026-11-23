---
id: PLMCP-98
title: Ask the user to confirm destructive actions in a dialog where the client can
status: To Do
assignee: []
created_date: '2026-11-23T18:44:53Z'
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
- [ ] #1 A new decision records the choice and supersedes the parts of decision-6 and decision-15 that rule elicitation out; doc-1 5.5, doc-2 and docs/fastmcp-reading-guide.md agree with it
- [ ] #2 A call without confirm on a client that declares form elicitation shows the user the same effect text as the refusal, and runs only after the user accepts, on both protocol eras
- [ ] #3 Decline, cancel, a malformed answer and a timeout change nothing and return an error that tells the assistant not to retry on its own
- [ ] #4 Clients that do not declare elicitation get exactly the present refusal and confirm=true flow
- [ ] #5 An acceptance applies only to the call and the effect that was shown: changed arguments, or a target whose effect changed in between, ask again and change nothing
- [ ] #6 One mechanism covers every tool with a confirm parameter, guarded by a registry test with a self-test; no tool is added to the default profile
- [ ] #7 Error handling, logging, redaction, output schemas and read-only mode are verified on rounds that ask; the dialog text and answers are not logged and never hold a secret
- [ ] #8 Unit and E2E tests drive accept, decline, cancel and no-handler on a handshake and a 2026-07-28 connection, over stdio and HTTP, including a call that waits longer than the usual request timeouts
- [ ] #9 The behaviour is tried in Claude Code and one further real client, and the documentation says which clients were seen to show the dialog
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
