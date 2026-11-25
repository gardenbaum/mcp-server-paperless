---
id: decision-94
title: >-
  Acknowledging all tasks needs a yes after a preview, and the task overview
  says what each part counts
date: '2026-11-25T11:46:19Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-89 AC 1, 3 and 7: how many tasks need attention, what runs now and how each type did is not readable; acknowledging takes up to 100 ids a call; and `run_task` must offer every type Paperless lets a user start.

On 2026-11-24 the owner approved the design: asked in a question dialog quoted in decision-93, the owner chose "Alle wie empfohlen" (all as recommended). So decision 1 of the draft (filters Paperless ignores) is an owner-approved exclusion of 2026-11-24, and its decisions 6a (`all: true` with a preview) and 3 (three types) are approved.

Probed on Paperless 3.5.1 (CI run 45604535090, job 139171854815 "e2e (1/6)", temporary tests of PLMCP-89.1):

- **P1**: `status_counts` takes every filter of the list but `status` and `is_complete`, which it ignores; its schema declares none. `active` and `summary` declare all and ignore them, also a type that does not exist. `days` of 0, -1 and 1000 are silently changed to 1 to 365; text is a 400. Whoever may view the system status (staff, superusers, `view_system_monitoring`) gets a summary of every user's tasks, also without `view_paperlesstask`, with which a user sees only own and unowned tasks in the list.
- **P3**: `acknowledge` with `all` marked the one new task the user may see, a waiting one; the tasks of others stayed. A second call answers 0. Ids of a task the user may not see are a 400. Without `change_paperlesstask` it is a 403.
- **P4, `llm_index`** (an embedding server inside the container that recorded what it was sent): nothing with the index off, and the task is a success; without an index every document; with an index and no change nothing; after a change of one document that document's notes, custom fields and text; with another embedding model every document.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **`get_task_overview`** (toolset `tasks`, read-only) reads `status_counts`, `active` and `summary`. The filters are sent for the counts only and the tool says so; a `days` outside 1 to 365 is refused instead of silently changed. A part Paperless refuses is null and named in `unavailable`; a user who may see no part gets the error.
- **Whose tasks the summary counts** is said: every user's, own and unowned ones, or unknown where the user settings, which carry the user's rights, may not be read.
- **Acknowledging all** sends `{"all": true}` and needs a yes (supplements decision-26, under which acknowledging needed none; with ids it still needs none). Preview (`dry_run`) and confirmation name how many tasks, how many of them failed or cancelled, waiting or running and completed, how many are not the user's own, the ten newest failures with type, quoted file name, id and time, that it cannot be taken back, and that tasks that come in meanwhile are marked too.
- **The yes is bound** to the counts and the newest task: a new task, one that ended or another one asks again. What comes in between the last read and the call is marked too (Paperless has no bound request); the result says how many if Paperless marked more than the preview had.
- **Nothing new is no call. Nothing is sent twice**: after a lost answer the counts are read once; none left means done, else it is unknown and said. A call that never reached Paperless or was refused is an error.
- It is not destructive by hint, because nothing is deleted, but takes `confirm`.
- **`run_task`** says that Paperless starts only three types by hand and one per call, with one call per type as the way to start several, and what `llm_index` sends as probed. Its confirmation says the same. The earlier text, that it rebuilds the index and sends every document, was wrong.
- **Left out, owner-approved as quoted above (2026-11-24)**: the filters of `GET /api/tasks/active/` and of `GET /api/tasks/summary/` (but `days`): Paperless ignores them, and sending them would promise something false.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A confirmed acknowledgement of all costs up to six reads and one write. The reads need `view_paperlesstask` besides `change_paperlesstask`.
- Between the last read and the call, milliseconds, a failure nobody has looked at can be marked as seen; it stays in the list.
- The counts of the overview and its summary can differ, because the summary may count tasks the user cannot list.
- The guide to the toolsets is 20 bytes longer (of the approved 89).
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

- Reading all ids first and sending those: exact, but one request per 100 tasks, and the `all` of the API would need an exclusion.

## Enforcement

- `tests/adapter/test_task_lists.py`, `tests/services/test_task_lists.py`, `tests/tools/test_tasks_toolset.py` (marker decision-94); `tests/contract/test_task_lists.py` on the fake and Paperless 3.5.1; `tests/e2e/test_task_lists.py`, which starts the three types.
- `tests/quality/test_api_coverage.py`: the exclusions need the quote and the date from this file.
