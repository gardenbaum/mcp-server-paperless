---
id: decision-93
title: >-
  The task tools have a toolset of their own and show of a task's input only the
  file name
date: '2026-11-25T11:46:19Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-89 AC 2, 3, 4 and 7: `list_tasks`, `acknowledge_tasks` and `run_task` were in the toolset `admin`, beside user administration, so an agent that should look after imports needed the tools that manage users. A tool carries exactly one toolset as its tag (decision-4), so it cannot be in two. `list_tasks` took three of the filters of `GET /api/tasks/`.

On 2026-11-24 the owner approved the design: asked in a question dialog "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?" (do you approve the drafts with all recommended options, with the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010), the owner chose "Alle wie empfohlen" (all as recommended). So decisions 2 and 3 of the draft (`GET /api/tasks/{id}/` and the ten task types) are owner-approved exclusions of 2026-11-24, and its decisions 4a (a new toolset), 5a (the logs stay in `admin`) and 10 (guide limit +89) are approved.

Probed on Paperless 3.5.1 (CI run 45604535090, job 139171854815 "e2e (1/6)", temporary tests of PLMCP-89.1; P1, P3, P4 and P5 are in decisions 94 and 95):

- **P1, the list**: `task_type` given twice means either. An unknown owner, type or time is a 400, an unknown ordering is ignored. `name` finds part of the file's name and the words for the type ("consume"). Two orderings can be joined by a comma.
- **P2**: each of the ten types is a 400, "Task type '...' cannot be manually triggered"; two types in one call are a 400; a staff user who is no superuser gets a 403.
- **P6**: `input_data` of uploads holds file name, MIME type and the metadata asked for (title, owner id); of bulk operations the operation and the number of documents; of every other type seen nothing. A password given to remove_password is in no task. The error of a failed task can name a server path.
- The tasks of a deleted user have no owner any more: every user who may view tasks sees them.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Toolset `tasks`**: the three tools move there. It is opt-in like `admin`, whose summary loses the word tasks; the instructions and the overview name it under "off" by themselves. Logs, configuration and `bulk_download` stay in `admin`.
- **This breaks operators who offer `admin` alone for the task tools**: they add `tasks`. Release notes, README and configuration say so.
- **`list_tasks` takes every filter and ordering** of the endpoint: `state`, `task_type` and `trigger_source` one value or several, which Paperless takes as a repeated parameter; `acknowledged`, `is_complete`, `owner` (user name or id), `name`, `result`, `created_from` and `created_to` (with a time zone). Every ordering but that by creation has the newest first where values are equal, so that pages do not overlap.
- **Refused here, because Paperless would ignore it and answer every task**: an empty list, a blank text.
- **Of a task's input only the file name is read.** `input_data` of an upload also holds a server path and the metadata asked for, such as a title and an owner. A row shows `file_name`, the owner's id and the waiting time.
- **`run_task` keeps the three types** Paperless starts by hand; decision-26 (a yes for two of them) is unchanged.
- **Left out, owner-approved as quoted above (2026-11-24)**: `GET /api/tasks/{id}/` (`get_task` reads the same row by the `task_id` every row shows); the ten task types `POST /api/tasks/run/` lists and refuses with 400.
- Supplements decisions 4, 26 and 41 and changes none of their rules.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The guide to the toolsets is 49 bytes longer in a session with `core` alone (of the approved 89).
- `list_tasks` still works with a single value per filter, so callers of 0.4 need no other change than the toolset.
- `name` also matches the words for type and source, in the language of the server's Paperless user; the argument says so.
- `AdminReader.list_tasks` is gone, also its time window of decision-88 (Consequences): `explain_workflow` and `explain_mail_import` read `TaskListReader.list_tasks`. `explain_mail_import` asks only for failed files that came by mail (`email_consume`), so the number it leaves out is exact, and failed uploads of other kinds no longer make its answer incomplete; before, it looked for them among the newest 25 failed files of any kind.
- Where tasks without a value come in an ordering is the choice of the database.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

- The tools stay in `admin`: no break, but the reason for this task stays.
- Only new tools in `tasks`: two places for one subject.

## Enforcement

- `tests/adapter/test_task_lists.py`, `tests/services/test_task_lists.py`, `tests/tools/test_tasks_toolset.py` (marker decision-93); `tests/contract/test_task_lists.py` on the fake and Paperless 3.5.1, the ten refused types included; `tests/e2e/test_task_lists.py`.
- `tests/quality/test_api_coverage.py`: the exclusions need the quote and the date from this file.
