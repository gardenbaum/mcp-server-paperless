---
updated_date: '2026-11-25T13:25:20Z'
id: decision-96
title: The confirmation of a change grant on a saved view says what it gives
date: '2026-11-25T11:41:41Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-88.3: `set_permissions` shares saved views (decision-91). A view looks harmless, but `run_saved_view` and the bulk tools choose documents by it, so its holder of a change grant can change which documents such a call sees or works on. Mail accounts and rules have a note of this sort in their confirmation (decision-87). The independent review of PLMCP-88.3 asked for one here. It is not part of the design the owner approved on 2026-11-24. Asked whether the confirmation should say in one sentence that whoever may change a saved view decides which documents run_saved_view and bulk actions see, the owner approved it on 2026-11-25 with "Alles wie empfohlen" (all as recommended).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- The confirmation of `set_permissions` for saved views ends with "Whoever may change a saved view decides which documents run_saved_view and bulk calls that name it see", wherever the call lets somebody change a view who may not now (a new change grant, another owner, no owner). A view grant or a withdrawal needs none.
- Nothing else of the confirmation changes. Supplements decisions 19 and 91; their rules are unchanged.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The dialog gets one sentence longer.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_permissions.py` (marker decision-96): where the note stands and where not.
