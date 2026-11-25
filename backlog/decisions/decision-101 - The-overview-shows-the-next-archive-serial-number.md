---
id: decision-101
title: The overview shows the next archive serial number
date: '2026-11-25T15:02:39Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-83 AC 1 names the next archive serial number. The core toolset is full (20 tools, decision-34), so a tool of its own would be off by default, and filing paper is a core job. On 2026-11-24 the owner approved the design of PLMCP-83 with all recommended options: asked "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?" (do you approve the drafts with all recommended options, with the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010?), the owner chose "Alle wie empfohlen" (all as recommended).

Probed on Paperless 3.5.1 (CI run 45638730209, job 139291151398 "e2e (1/6)", a temporary test of PLMCP-83.2): `next_asn` is the highest number of all documents plus 1, those hidden from the user too. The trash is not counted, but a number of a trashed document is refused on writing. A user who may not view documents gets a 403.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `get_instance_overview` shows it as `next_asn`, null if refused; one more request per call. Supplements the budget reasoning of decision-34: no tool is added to core.
- The name is short because the definition limit is 1010 bytes (measured 1010): the long name would have left no room to say that the trash is not counted.
- Nothing is reserved. The documentation says that Paperless refuses a number that another document, or one in the trash, has.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- It is a guess the moment it is read: two callers get the same number, and Paperless refuses the second.
- Every read of the overview, the startup probe included, sends one request more.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/adapter/test_instance.py`, `tests/contract/test_instance_reader.py` (hidden documents, trash, a refused user) on the fake and Paperless 3.5.1, `tests/tools/test_core.py`, `tests/e2e/test_lookups.py`.
- `tests/tools/test_toolset_budget.py`: the definition limit.
