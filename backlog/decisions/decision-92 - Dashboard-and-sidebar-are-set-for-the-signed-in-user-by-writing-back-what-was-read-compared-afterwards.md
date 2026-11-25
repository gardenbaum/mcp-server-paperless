---
id: decision-92
title: >-
  Dashboard and sidebar are set for the signed-in user by writing back what was
  read, compared afterwards
date: '2026-11-25T10:41:42Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-88 AC 2: whether a saved view is on the dashboard or in the sidebar can be read and written without losing other settings. In API version 10 both are lists of ids in the UI settings of a user (`settings.saved_views.dashboard_views_visible_ids` and `sidebar_views_visible_ids` of `/api/ui_settings/`); version 9, which merged them on the server, is ruled out by decision-2. A POST replaces all settings of the user and no conditional write exists. Probed on Paperless 3.5.1 (decision-91, P1, P3, P5).

On 2026-11-24 the owner approved the design with its recommended options, "Alle wie empfohlen" (all as recommended), in a question dialog quoted in decision-91: here the numbers 3 (through `/api/ui_settings/`, compared afterwards), 4 (a tool of its own), 5 (computed keys are not sent back), 6 (no confirmation) and 8 (guide limit +17).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **`pin_saved_view`** puts a view on the dashboard or into the sidebar or takes it away, for the Paperless user this server signs in as, and says so. It works for every view that user sees, so it is no argument of `update_saved_view`. Not destructive, idempotent, closed world, no confirmation: only the user's own web UI changes, and back again. Tools that return a view show `on_dashboard` and `in_sidebar` (null where the settings cannot be read, which never fails the call).
- **Written back as read.** Everything stored is sent back unchanged with the two lists; only the id of the view is added at the end or removed. What Paperless computes for every answer (eight keys, the OAuth addresses with their state, `update_checking.backend_setting`) never leaves the adapter and is not sent. At most 1 MiB is read. Where nothing changes, nothing is sent.
- **Nothing is repaired**: where `saved_views` is no object or a list to change is no list, the call is refused.
- **Compared afterwards** with a fresh read, type by type. A difference is an error that names keys, never values, and says that someone else may have saved meanwhile. A lost answer, or one that is not from Paperless (a redirect, a login page), is looked up once the same way; else the result is not known and the error says so, with what the late change would do. Nothing is ever sent twice.
- **One call at a time** in this server: assistants call tools in parallel, and two writes at once would undo each other. A call waits at most 40 seconds for another one and is refused then.
- Supplements decision-2; its rules are unchanged.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A save of an open web UI tab (or another server) between the read and the POST is lost, and its next save undoes the pin: noticed if it shows in the second read, else invisible. The tool description says so.
- A POST that arrives late (the call was cancelled or timed out) writes back all settings as they were read before it, and so undoes what was saved since. A cancelled call cannot say so; a timed-out one does.
- A key a later Paperless computes would be stored as junk, as the web UI does; the contract test on the latest release shows it.
- Every list or read of views costs one request more. Ids of deleted views stay in the lists.
- With one token for several people it is the dashboard of that token's user.
- The other UI settings, the order of the pins among them, belong to PLMCP-83.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_view_pins.py` (marker decision-92): the matrix of pins, other writers, lost answers, malformed settings, parallel calls, and as a property that nothing else changes. `tests/adapter/test_ui_settings.py`: requests, limit, and as a property that computed keys are left out.
- `tests/contract/test_ui_settings.py` on the fake and Paperless 3.5.1, `tests/tools/test_views.py`, `tests/e2e/test_view_pins.py` (memory, stdio and HTTP).
