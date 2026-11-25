---
id: decision-88
title: >-
  explain_workflow infers from the state of today and takes a log record only by
  the consume task
date: '2026-11-25T09:36:01Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-87 AC 3: why a workflow did not run on a document is explained from its settings, the document's history and logs; the API keeps no run record beyond that, and the answer says so. On 2026-11-24 the owner approved the design with its recommended options, "Alle wie empfohlen" (all as recommended; the question is quoted in decision-81): here the numbers 7a (log lines for staff users, without other documents' names and text) and 8a (regular expressions and fuzzy matching are not checked).

Probe P6 on Paperless 3.5.1 (CI run 45585312563, job 139105284889 "e2e (1/6)", temporary test): the log holds "Document did not match Workflow: NAME" (INFO), the reason in the next record (DEBUG), "Document matched WorkflowTrigger N from Workflow: NAME" and "Applying WorkflowAction N from Workflow: NAME". In consumption and for added documents each has the first eight characters of the consume task in brackets, after an update none. The consume task names the document in `related_document_ids`, is created before the document is `added` and is found by `date_created_after` and `date_created_before`. Changes by a workflow have no actor in the history. `custom_field_query` with `id__in` answers a count, an invalid query 400.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **`explain_workflow`** (read-only) compares the settings of today with the document of today and begins by saying so. Per trigger it lists the checks in the order of Paperless 3.5.1 with ok, no or unknown and the reason, and `would_match_now` with the first no.
- **Exact** are tags, correspondents, types, storage paths, the file name of triggers other than consumption (any document without a name passes, except on a schedule), content by any, all and exact (the newest version's text, never shown) and the custom field query (asked of Paperless; no expression, or one it rejects, matches nothing, any other refusal is unknown). The order is that of the trigger's type: a schedule looks by date, references, query and file name first and reads the text of what it found.
- **Schedules**: added and modified are compared as moments, created and a custom field as days of Paperless, unknown where two days are possible near midnight. Whether a schedule ran before (once, or within its interval) cannot be read, so a schedule that fits is unknown, never yes. An offset too large to calculate is unknown and named.
- **Unknown** are regular expressions and fuzzy matching, and source, path, mail rule and file name of a consumption: the name is told as stored, but a part of an e-mail or a split, or a cleaned name, was consumed under another one. (The draft called the file name exact; the review of 2026-11-25 found it is not.)
- **`did_run` is likely, never yes**: a log record with the prefix of one of the document's consume tasks, written while that task ran, says that it matched or an action was applied. Whoever may upload or change documents can put a whole record into a file name or title, and may learn the id of the own task, so it is no proof; the answer says so. Otherwise unknown, never no. Records without a task within two minutes of the document's arrival or of a change in its history are "perhaps"; others are left out.
- **Time zone**: the log has local times, the history UTC, the document's own timestamps the offset Paperless had then. A moment is compared with the offset of the document's timestamp within a day of it; where there is none, with each known offset and an hour before and after (summer time).
- **The log is told in the server's words**: which trigger matched, which action was applied, that it did not match and why, by a list of the reasons Paperless writes. A record counts only with its whole text and its logger (`paperless.matching`, `paperless.handlers`), so a workflow whose name begins like another, or a text inside another message, is not taken; the reasons are told by beginning and form of the whole text (what did not fit, not which rule of it), because names in them may hold any words. No line is repeated, so no name, text or file of another document comes through; a reason the list does not know is left out. Only for staff users, at most 20 records of the 1,000 lines Paperless gives; the answer says where they begin.
- **What is missing is named** in `not_checked`: no right to the audit log, tasks or log, an error, or the time (nothing is asked 40 seconds after the call began, also no further page of names or workflows), a log that is empty or begins after the document arrived. At most 50 history entries, 100 consume tasks of the day before the document arrived, 20 findings (the likely ones first), 10 names per check and 50,000 characters; what is left out is named and counted, and what fails many times is named once with its number.
- **Shared helper**: `services/log_records.py` reads the end of a log as records without credentials, for PLMCP-81.4 (mail) too, which owns it.
- On 2026-11-25 the owner approved the coarser reasons from the log ("the tags do not fit"), with "Alles wie empfohlen" (everything as recommended).
- On 2026-11-25, in a question dialog, the owner chose "Ja, alles (Recommended)" (yes, everything (recommended)): the answer is likely instead of yes, and the file names of mail and barcode documents and past runs of schedules are unknown.
- Supplements decisions 21 and 66.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The answer can mislead where document or workflow changed since: the first sentence and `last_change` are the guard.
- Runs after a change, on a schedule or by hand write no task into the log, so they are at most "perhaps".
- A clock of this server that differs from that of Paperless shifts what a schedule has reached.
- Log records under a former name of the workflow are not found (the log has names only); the answer says so.
- The reasons of the log are coarser than the log itself ("the tags do not fit"): a name in a longer form could imitate another rule.
- If Paperless changes the words of its log, fewer findings are told, none wrongly.
- `AdminReader.list_tasks` takes a time window; the admin tools do not offer it (PLMCP-89).
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/services/test_workflow_explanation.py` and `tests/services/test_log_records.py` (marker decision-88): every filter both ways, order, schedule, history, log, limits, rights; content matching as a property against a copy of Paperless's source.
- `tests/adapter/test_admin.py`, `tests/contract/test_admin.py` (time window of tasks, fake and 3.5.1), `tests/tools/test_workflows.py`, `tests/e2e/test_workflows.py` (a matching, a non-matching and a consumed document, a restricted user).
