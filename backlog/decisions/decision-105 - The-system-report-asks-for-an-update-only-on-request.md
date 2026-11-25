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

The owner was asked to accept three points: the field name `next_asn` (instead of `next_archive_serial_number`), that `test_storage_path` refuses documents that refer to anything the caller may not see, and that the update check counts an unset PAPERLESS_ENABLE_UPDATE_CHECK ("default") as allowed. The owner decided on 2026-11-26: "Bitte alles wie von dir empfohlen." (please everything as recommended by you); the recommendation was to accept all three.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `get_system_report(check_for_update=true)`, false by default, supplementing decision-95: `update` holds the newest release and whether it is newer, both null, with a note that says why, where Paperless could not learn it, where the check took more than 10 seconds or where it failed in any way; the report itself stays. It is asked once, never repeated.
- **`PAPERLESS_ENABLE_UPDATE_CHECK` is honoured**, as `ui_settings` reports it (`update_checking.backend_setting`; source of 3.5.1: true, false or "default" where it is not set). Paperless's endpoint itself ignores it. GitHub is asked only where it is true or not set; where it is off, has any other value or cannot be read (no permission to view UI settings, an error), nobody outside is asked and the answer is "unknown: switched off" or that it could not be read. Not set counts as allowed, because the caller asks explicitly; the web UI then asks its user.
- Without the option `update` is null and nothing is asked.
- The tool has the open-world hint: on request something outside Paperless is asked, by Paperless.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The E2E stack switches the check off, so no test needs GitHub. The answer with the check on was seen on 3.5.1 in the probe only.
- A server without internet makes the call up to 10 seconds longer.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/services/test_instance.py`, `tests/adapter/test_instance.py`, `tests/contract/test_instance_reader.py` on the fake and Paperless 3.5.1, `tests/tools/test_tasks_toolset.py`, `tests/e2e/test_task_lists.py`.
