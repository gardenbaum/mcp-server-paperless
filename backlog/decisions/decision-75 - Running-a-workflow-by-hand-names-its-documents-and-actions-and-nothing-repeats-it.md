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
- **The confirmation names the workflow, every action and every document (at most 100)**, the actions before the documents and the most far-reaching first (sending, trash, owner and rights, AI, passwords, unknown ones), each with its step ("step 2 of 5"), because the order decides what is left; Paperless moves to the trash last, which its clause says. It names what an assignment or removal writes or takes away with its values (names and texts quoted and cut like titles; an object the user cannot see by its number; select labels and titles of linked documents instead of ids; of custom fields only those Paperless writes, the ones in `assign_custom_fields`), and what decision-21 and decision-44 name, as they show it: recipients and webhook hosts in full, at most 20, refused otherwise; the actions this server cannot write by what they do: for AI suggestions the fields, whether existing values are overwritten and whether tags, correspondents and document types are created.
- **Tags that come along are named**: the parent tags of assigned ones and the child tags of removed ones, as decision-71 does; that child tags the user cannot see come off, too, is always said. They are named and not refused: the workflow is stored as it is, and Paperless takes the child tags whatever the caller asks, so a refusal would only make such a workflow unusable here.
- The contents of e-mails and webhooks are not shown. The text also says whether trigger filters apply and that Paperless does not tell which documents they skip (a filter is only what Paperless reads of a manual trigger: content matching with an algorithm, file name, tags, references, custom field query; not sources, path, mail rule or schedule); without `apply_filters` that each document counts as matching.
- **Bound are** the workflow with all triggers and actions as read (also what is not shown), `apply_filters`, the documents in their order with their current values and owners, and the newest file versions.
- **Selection as decision-69 and decision-71**: a filter or a view alone is never confirmed by `confirm=true`; ids with it only while they all match. A run leaves documents matching, so more than 100 matches of a filter are refused.
- **Known beforehand and refused**: a workflow that is not enabled or has no manual trigger, a document the user may not change or, where the user may be read (`view_uisettings`), does not own, and a missing global permission to change documents, where Paperless lists the user's. The preview names these documents; it does not repeat the actions, which are in its `actions`. Everything else Paperless refuses as a whole, and nothing ran.
- **How it ended**: with the task's own words only when they are exactly Paperless's for this workflow and name documents of this run; then `failed` names them, as a result that says so first, or as an error if it failed on all. Any other failed task, a lost answer, the plain-text 400 Paperless gives when queueing failed, and a run that cannot be followed (60 seconds, HTTP 429 at once, no permission to view tasks) say that it is not known what ran. Nothing is read again, because the documents do not show whether an e-mail went out, and **nothing is ever sent again**; each of these, and a partial failure, tells the assistant not to run the workflow again on its own.
- The answer says that the results of e-mails, webhooks, password removals and AI suggestions are not told, where the workflow has such an action.
- **A confirmation above 100,000 characters is refused**, with a plain error and no dialog, also with `confirm=true`, and never cut: otherwise a run could be confirmed whose end nobody reads. Counted is the whole text a client without dialogs shows, the hint of the next call included (a dialog shows less). The error tells the length, the limit and how much of it are actions and documents; the hint is fewer documents, or, where the shortest one alone is too much, splitting the workflow or the step that is too long alone, or the web UI. Missing rights are told first. The preview still works: it says that the run would be refused, with the sizes, the ids and the actions that fit: one that does not is skipped and the next are shown, so a destination, the trash or a change of rights is in it wherever it fits (`incomplete` and the message say how many steps are left out). The bound of the preview applies to `actions`; its message names the documents, at most 100, which is about 25,000 characters at most measured.
- On 2026-11-24 the owner approved, as recommended by the main agent, with "Alles wie von dir empfohlen." (everything as you recommend): failing e-mails and webhooks do not fail the run; a failure on some documents is a result; the contents of e-mails and webhooks are bound, not shown; tags that come along are named, not refused; a confirmation above 100,000 characters is refused, also with confirm=true, and never cut.
- Manual triggers are still not written by this server (PLMCP-87 decides).
- Supplements decisions 6, 21, 44, 69 and 71. Supersedes nothing.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A user without `view_paperlesstask` never learns how a run ended; one without `view_uisettings` learns about a foreign document from Paperless's refusal.
- A call without a dialog and `confirm=true` is checked against its own read only (the server keeps nothing between calls, as in decision-71).
- Between the last read and the task someone can still change the workflow or a document: Paperless runs what is stored then, also when the workflow was disabled meanwhile.
- A document that was moved to the trash between the request and the task is skipped without a word.
- Measured lengths of a confirmation for 100 documents and six actions with 19 long e-mail addresses, 20 tags that come along and ten field values: 12,020 characters with ordinary titles and 42,924 where every character of titles, names and values is escaped (the previews 7,164 and 24,564). The maximum is the limit of 100,000; a workflow that alone exceeds it can be run in the web UI only.
- The list of tags is read anew in every call whose workflow assigns or removes tags, unlike in bulk changes, where the caller names the tags and a child tag that stays refuses: here a child tag made in the last minutes would come off unnamed. Other names come from the resolver's cache and can be some minutes old; the ids are bound.
- If Paperless changes the words of the failed task, partial failures become "not known what ran".
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/services/test_workflow_runs.py` (marker decision-75): the text for every kind of action and filter, what an acceptance binds, as a property that no call by filter or view alone runs anything, restricted users, every end. `tests/adapter/test_workflow_runs.py`: the request and the task's words. `tests/tools/test_run_workflow.py`: hints, dialogs on both protocol eras, required dialogs. `tests/tools/test_registry.py` requires `confirm` and the way back from the trash.
- `tests/contract/test_workflow_runs.py` and `tests/e2e/test_run_workflow.py`: filters, rights, unusable workflows, a partial failure, trash, e-mails and webhooks that cannot be sent, on Paperless 3.5.1.
