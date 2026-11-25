---
id: decision-86
title: >-
  Manual triggers and AI suggestions are written, password removal stays with
  the web UI
date: '2026-11-25T08:19:44Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-21 lets only some types be written: manual triggers (Paperless 3.5), applying AI suggestions and password removal were left to the web UI. `run_workflow` runs manual triggers since decision-75, and its wording tells what AI suggestions do. PLMCP-87 AC 5 wants the rest covered or left out with the owner's approval.

On 2026-11-24 the owner approved the design with its recommended options: "Alle wie empfohlen" (all as recommended), the question is quoted in decision-81. Here the numbers 4a (manual triggers are written), 5a (AI suggestions are written and confirmed, password removal is not) of the draft; so password removal is an owner-approved exclusion of 2026-11-24.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Manual triggers (type 5)** are written by `create_workflow`, `update_workflow` and `update_workflow_part`, with the filters Paperless reads of them (decision-75): content, file name, tags, correspondents, document types, storage paths and custom field query. Sources, path, mail rule and schedule, which Paperless stores and ignores, are refused. A confirmation says that the workflow runs when a user runs it by hand.
- **Applying AI suggestions (type 8)** is written with its fields, whether missing objects are created and existing values overwritten. While the workflow is enabled it is always confirmed, in words run_workflow uses, too (truer than those of decision-75): the AI backend is asked about the text of every matching document, for which fields, over existing values or not, creating objects or not. It needs a field and a trigger other than consumption; on consumption triggers of the same workflow it does nothing, which the confirmation says. The text names only what the selected fields can create, that Paperless asks at least once per document (it repeats after a timeout or rate limit), and that title and created change only over existing values, because they always have one.
- **Left out, owner-approved as quoted above (2026-11-24)**: writing password removal (type 5 of actions, `passwords`). The passwords would go through the chat and could neither be shown nor kept when they are replaced. It is still listed, moved, removed and run.
- **Queued documents**: Paperless queues the action per document and reads its settings when the task runs. A changed action counts for documents already queued, a removed one drops them; the tool texts say so.
- A matching algorithm without a match, which matches nothing, is refused on manual triggers.
- Replacing the triggers is refused only where that would drop an unknown type, replacing the actions where it would drop password removal or an unknown type.
- On 2026-11-25, in a question dialog, the owner chose "Beides ja" (yes to both): the file name filter stays refused on triggers other than consumption and manual, and AI suggestions without a backend stay a documented limit.
- Supersedes the list of writable types in decision-21 and the sentence of decision-75 that manual triggers are not written; everything else in both stands.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- An assistant can build a workflow that asks the AI backend once per matching document; the user is told before it is enabled.
- Paperless accepts AI suggestions without a configured backend (decision-81, P5); they then do nothing. The server does not say so when the action is written: whether AI is on is only in the UI settings, which need a right of their own, and the workflow tools do not read them; `get_instance_overview` shows it (`ai_enabled`).
- The measured confirmations of decision-75 are 56 characters longer (12,076 and 42,980), far below its limit of 100,000.
- Paperless checks a workflow's AI action against its triggers at every update, also of its name.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/services/test_workflows.py`, `tests/services/test_workflow_parts.py`, `tests/tools/test_workflows.py` (marker decision-86); `tests/contract/test_workflows.py` on the fake and Paperless 3.5.1; `tests/e2e/test_workflows.py`, where a manual trigger written here is run.
- `tests/quality/test_api_coverage.py`: the exclusion needs the quote and the date from this file.
