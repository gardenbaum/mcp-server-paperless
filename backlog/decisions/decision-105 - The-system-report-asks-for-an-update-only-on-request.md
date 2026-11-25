---
id: decision-105
title: The system report asks for an update only on request
date: '2026-11-25T15:46:14Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
On 2026-11-24 the owner approved the design of PLMCP-83 with all recommended options ("Alle wie empfohlen", all as recommended; the question is quoted in decision-99). Its decision 4: the update check (`GET /api/remote_version/`) is an option of PLMCP-89's `get_system_report`, off by default, because Paperless then contacts GitHub.

Probed on Paperless 3.5.1 (CI run 45638730209, job 139291151398, a temporary test of PLMCP-83.2): every signed-in user gets the answer (0.32 s); where GitHub does not answer, the source says version `0.0.0`.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `get_system_report(check_for_update=true)`, false by default, supplementing decision-95: `update` holds the newest release and whether it is newer, both null where Paperless could not learn it, where the check took more than 10 seconds or where it failed as unavailable; the report itself stays. It is asked once, never repeated.
- Without the option `update` is null and nothing is asked.
- The tool has the open-world hint: on request something outside Paperless is asked, by Paperless.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A server without internet makes the call up to 10 seconds longer.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_instance.py`, `tests/adapter/test_instance.py`, `tests/contract/test_instance_reader.py` on the fake and Paperless 3.5.1, `tests/tools/test_tasks_toolset.py`, `tests/e2e/test_task_lists.py`.
