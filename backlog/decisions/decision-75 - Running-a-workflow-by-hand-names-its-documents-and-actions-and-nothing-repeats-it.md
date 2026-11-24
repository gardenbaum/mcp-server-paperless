---
id: decision-75
title: >-
  Running a workflow by hand names its documents and actions, and nothing
  repeats it
date: '2026-11-24T18:34:57Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Paperless 3.5.1 runs a workflow with a manual trigger on chosen documents (`bulk_edit` with `run_workflow`). Read in the source of 3.5.1: it needs the permissions to view workflows and to change documents and ownership (or no owner) of every document; the workflow has to be enabled; it is checked as a whole first and then runs as a queued task, document by document. A failure on one document does not stop the others; the failed task names the documents in its text, and what the earlier actions of such a document did stays. With `filter_documents` the documents that match no manual trigger are skipped silently. E-mails that cannot be sent only show in the log, webhooks, password removals and AI suggestions are queued further, so none of them fails the task. Actions can move documents to the trash, send them away, and change owner and permissions, and running them again does all of it again.

decision-21 covers workflows that run by themselves. The owner approved on 2026-11-24 the design, with number 11a (a tool of its own, `run_workflow`, in the toolset `workflows`) and 14 (the guide limit to at most 2943): "Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits." (draft 80: numbers 1 to 20 as you recommend, with the limits named there) (PLMCP-80).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Every run is confirmed** (or previewed with `dry_run`), also one that only files. `run_workflow` is destructive, reaches the outside world and is not idempotent, which is why it is no operation of `bulk_edit_documents`.
- **The confirmation names the workflow, every document (at most 100) and every action**: what an assignment or removal writes or takes away with its values (names and texts quoted and cut like titles; an object the user cannot see by its number), and what decision-21 and decision-44 name, as they show it: recipients and webhook hosts in full, at most 20, refused otherwise; the actions this server cannot write by what they do. The contents of e-mails and webhooks are not shown. It says whether trigger filters apply and that Paperless does not tell which documents they skip; without `apply_filters` that each document counts as matching.
- **Bound are** the workflow with all triggers and actions as read (also what is not shown), `apply_filters`, the documents in their order with their current values and owners, and the newest file versions.
- **Selection as decision-69 and decision-71**: a filter or a view alone is never confirmed by `confirm=true`; ids with it only while they all match. A run leaves documents matching, so more than 100 matches of a filter are refused.
- **Known beforehand and refused**: a workflow that is not enabled or has no manual trigger, a document the user may not change or, where the user may be read (`view_uisettings`), does not own. The preview names these documents. Everything else Paperless refuses as a whole, and nothing ran.
- **How it ended**: with the task's own words only when they are exactly Paperless's for this workflow and name documents of this run; then `failed` names them, as a result that says so first, or as an error if it failed on all. Any other failed task, a lost answer and a run that cannot be followed (60 seconds, HTTP 429 at once, no permission to view tasks) say that it is not known what ran. Nothing is read again, because the documents do not show whether an e-mail went out, and **nothing is ever sent again**; each of these, and a partial failure, tells the assistant not to run the workflow again on its own.
- The answer says that the results of e-mails, webhooks, password removals and AI suggestions are not told, where the workflow has such an action.
- Manual triggers are still not written by this server (PLMCP-87 decides).
- Supplements decisions 6, 21, 44, 69 and 71. Supersedes nothing.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A user without `view_paperlesstask` never learns how a run ended; one without `view_uisettings` learns about a foreign document from Paperless's refusal.
- A call without a dialog and `confirm=true` is checked against its own read only (the server keeps nothing between calls, as in decision-71).
- Between the last read and the task someone can still change the workflow or a document: Paperless runs what is stored then, also when the workflow was disabled meanwhile.
- A document that was moved to the trash between the request and the task is skipped without a word.
- The confirmation of a workflow with many values is long; its length is not capped, as in decision-71 and decision-73.
- If Paperless changes the words of the failed task, partial failures become "not known what ran".
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/services/test_workflow_runs.py` (marker decision-75): the text for every kind of action and filter, what an acceptance binds, as a property that no call by filter or view alone runs anything, restricted users, every end. `tests/adapter/test_workflow_runs.py`: the request and the task's words. `tests/tools/test_run_workflow.py`: hints, dialogs on both protocol eras, required dialogs. `tests/tools/test_registry.py` requires `confirm` and the way back from the trash.
- `tests/contract/test_workflow_runs.py` and `tests/e2e/test_run_workflow.py`: filters, rights, unusable workflows, a partial failure, trash, e-mails and webhooks that cannot be sent, on Paperless 3.5.1.
