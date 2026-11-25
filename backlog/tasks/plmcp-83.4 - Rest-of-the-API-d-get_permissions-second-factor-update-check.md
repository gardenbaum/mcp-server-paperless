---
id: PLMCP-83.4
title: 'Rest of the API d: get_permissions, second factor, update check'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T14:42:19Z'
updated_date: '2026-11-25T17:48:55Z'
labels: []
dependencies:
  - PLMCP-83.3
  - PLMCP-89
parent_task_id: PLMCP-83
type: feature
ordinal: 110000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Last part of PLMCP-83; needs PLMCP-89.3 (get_system_report). Design approved by the owner on 2026-11-24 with all recommendations ('Alle wie empfohlen', all as recommended); file design-plmcp-83.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 get_permissions shows owner and grants of 1 to 100 objects by name without changing them
- [x] #2 update_user(remove_second_factor=true) asks first and refuses the server's own account
- [x] #3 get_system_report(check_for_update=true) asks for the newest release within its own bound and says unknown where it cannot
- [x] #4 Nothing stands open under PLMCP-83 in docs/api-coverage.toml
- [x] #5 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. PLMCP-89 is merged. Tests first per level. 2. get_permissions in PermissionService (reader of set_permissions, 40 s for what is read one by one, 100,000 characters, what is left named). 3. AdminWriter.remove_second_factor behind update_user, in a call of its own, always confirmed, own account refused. 4. SystemReportReader.check_for_update behind get_system_report, 10 s, unknown where it cannot. 5. Decisions, guide limit +18, coverage rows, README sentence, docs, snapshots.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first at every level (58 failing or erring in 10 modules), then green. 32 hand mutants (one was no valid code): 4 survived, killed by new tests and re-run. decision-103 to 105. The second factor is removed in a call of its own. Nothing is open under PLMCP-83 in docs/api-coverage.toml; the README says the whole sentence.

Independent review, closed test first: M2 an unknown or new id lands in missing or is found for every kind (ids are read as they are, names from a list read anew). L1 users and groups are kept once read (test with a short ttl). L3 PAPERLESS_ENABLE_UPDATE_CHECK is honoured, fail closed; not set counts as allowed (decision-105). L4 any failure of the check is unknown with a note, the report stays. L5 an unreadable user after the removal says that it may have been applied. L6 a yes is for the account as shown (test); the contract test asks nobody outside (the E2E stack switches the check off).

Full E2E 45654514444 on 16d9a5d7: nine of ten jobs green; shard 5/6 red in test_a_second_factor_is_removed_only_with_confirm only. Cause, from the job log (139347500802) and the 3.5.1 source: the test made its user without view_document and signs_in reads /api/documents/, so a signed-in user gets 403; the removal had worked (mfa_enabled was asserted false on the line before). PaperlessBasicAuthentication answers 'MFA required' with 401, and basic authentication is not throttled. The check before ('not 200') had passed for the wrong reason. Now the user may view documents and the test asserts 200, then 401 with the factor, 401 after the refused call, 200 after the confirmed one. No code change.

The owner was asked to accept three points: the field name `next_asn` (instead of `next_archive_serial_number`), that `test_storage_path` refuses documents that refer to anything the caller may not see, and that the update check counts an unset PAPERLESS_ENABLE_UPDATE_CHECK ("default") as allowed. The owner decided on 2026-11-26: "Bitte alles wie von dir empfohlen." (please everything as recommended by you); the recommendation was to accept all three.

Criteria proven: full hosted E2E 45661300031 on 21cdf55c, all ten jobs green (contract tests on Paperless 3.5.1 and E2E of parts b, c and d, which that head contains). Definition of Done: tests first is left open for single tests written with or after their code (guard test, mutant kills); just check was not run as a whole locally; merge open.
<!-- SECTION:NOTES:END -->
