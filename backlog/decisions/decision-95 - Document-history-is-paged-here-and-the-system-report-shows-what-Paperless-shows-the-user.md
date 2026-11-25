---
id: decision-95
title: >-
  Document history is paged here, and the system report shows what Paperless
  shows the user
date: '2026-11-25T11:46:20Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-89 AC 5, 6 and 7: the history of a document older than its newest 50 entries is not readable, and statistics and system status give more than `get_instance_overview` shows.

On 2026-11-24 the owner approved the design: asked in a question dialog quoted in decision-93, the owner chose "Alle wie empfohlen" (all as recommended). So decision 1 of the draft (leave out what Paperless ignores) is an owner-approved exclusion of 2026-11-24, and its decisions 7a (`history_page`), 8a (a tool of its own in `tasks`) and 9a (all of the status, to those Paperless shows it to) are approved.

Probed on Paperless 3.5.1 (CI run 45604535090, job 139171854815 "e2e (1/6)", temporary tests of PLMCP-89.1):

- **P5, history**: a bare list, newest first, with unique ids; `page` and `page_size` change nothing. 20 corrections of a text of 10 KB make 394,726 bytes for 26 entries (each holds the old and the new text), compressed with zstd. A custom field whose name or value has a colon is cut at it, not an error. Entries of fields that were deleted are gone. Another user's document is a 403 (also with `view_logentry`), one without an owner is readable, an unknown one a 404.
- **Status**: for `view_system_monitoring`, staff and superusers; a 403 (text) for others. The database URL is a path with SQLite, the Redis URL has no credentials, the worker is `celery@<container>`. Statistics answer every user, of the objects the user may see.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **`get_document(include=["history"], history_page=n)`**: 50 entries a page, cut here from the whole list, with `page`, `next_page` and the total; `truncated` stays. A page past the end is an error that names the last one; a page other than 1 without the history is refused. Entries that come in between two calls shift the pages; ids are unique, and the argument says so.
- **The text never leaves the adapter**, on no page: a correction is an entry whose `content` only says that the text changed (PLMCP-87.4).
- **At most 64 MiB of a history are read** (the size used for the trash), counted unpacked, compressed or not; a larger one is refused with the reason instead of exhausting memory.
- **`get_system_report`** (toolset `tasks`, read-only) returns everything statistics and status give, as Paperless shows it to the user; the status is null where it is refused. Host, database, Redis and worker names are no secrets and are shown to whom Paperless shows them. Every text goes through the rule for URLs with credentials (decision-66).
- `get_instance_overview` stays as it is, and so does its definition.
- **Left out, owner-approved as quoted above (2026-11-24)**: `page` and `page_size` of `GET /api/documents/{id}/history/`, which Paperless ignores.
- Supplements decisions 41 and 66 and changes none of their rules.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Every page transfers the whole history, with every corrected text.
- A read of the history waits up to 60 seconds for the next bytes. The port says only that a history is too large; what to leave out is the caller's hint.
- The definition of `get_document` (default profile) grows; the number of tools of that profile does not.
- The guide to the toolsets is 20 bytes longer (the last of the approved 89).
- `status` can take seconds while no worker answers.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

- A tool of its own for the history: the default profile would not change, but the history would be in two places.
- The rest of the status in `get_instance_overview`: about 2.5 KB more in every session.

## Enforcement

- `tests/services/test_documents.py` (marker decision-95; the pages hold every entry once, as a property), `tests/adapter/test_documents.py`, `tests/adapter/test_instance.py` (planted credentials in every text of the status), `tests/tools/test_documents.py`, `tests/tools/test_tasks_toolset.py`; `tests/contract/test_instance_reader.py` and `tests/contract/test_document_reader.py` on the fake and Paperless 3.5.1; `tests/e2e/test_task_lists.py` (60 changes page by page).
- `tests/quality/test_api_coverage.py`: the exclusion needs the quote and the date from this file.
