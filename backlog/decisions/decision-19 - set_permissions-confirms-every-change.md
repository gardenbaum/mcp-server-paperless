---
id: decision-19
title: set_permissions confirms every change
date: '2026-11-14T03:25:00Z'
status: accepted
---
## Context

PLMCP-28 adds `set_permissions` for documents, tags, correspondents, document types and storage paths. The first design asked for `confirm` only to replace grants or change an owner, and let a merge (adding grants) run unconfirmed. But granting access is exposure that cannot be taken back, for the reason of decision-17: a user or group that was given view access may already have read, copied or passed on what it saw, and removing the grant later does not undo that. Decision-14 left sharing and permissions to `bulk_edit_taxonomy` (PLMCP-22); its line "Sharing and permissions are `bulk_edit_taxonomy` (PLMCP-22)" is outdated, because `set_permissions` now covers documents and taxonomy objects.

## Decision

- `set_permissions` requires `confirm=true` whenever the call would change anything, in `merge` mode too: a new grant, a replaced list, an owner change or an owner removal. Only a call that changes nothing (every object already is as asked) is neither sent nor confirmed, and it needs no confirm.
- The refusal uses `ConfirmationRequired.for_effect` (decision-15) and names, per object, the owner change and who gains and who loses view or change access. Long lists are capped like those of the other toolsets: the first objects are named in full, the rest are counted.
- `mode` still defaults to `merge`, and lists a call leaves out are never touched in either mode. The tool stays annotated destructive, so the registry test requires its `confirm` parameter.
- This amends decision-14 (permissions are no longer only `bulk_edit_taxonomy`, which keeps its own `set_permissions` operation with `dry_run` and confirm) and supplements decision-12 (owner changes) and decision-17 (publishing needs confirm); it changes none of their rules.

## Consequences

- Every sharing change takes two calls by design; a client that never passes `confirm` can read users and groups but cannot grant, replace or hand over anything.
- Re-running a call that already took effect is harmless and needs no confirm.
- The refusal text and the tool description are part of the contract; the surface snapshot shows them.

## Enforcement

- The registry test (tests/tools/test_registry.py) requires `confirm: bool = False` on every destructive tool, and `set_permissions` is one.
- Service tests (tests/services/test_permissions.py) show that a merge that adds a grant, a replace and an owner change are refused without `confirm`, that nothing is written (the fake is created without `allow_writes`), and that a no-op needs none.
- Tool tests (tests/tools/test_permissions.py) and E2E (tests/e2e/test_permissions.py) run the flow through the assembled server.
