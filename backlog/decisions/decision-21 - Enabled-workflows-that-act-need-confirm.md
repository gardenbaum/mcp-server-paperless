---
id: decision-21
title: Enabled workflows that act need confirm
date: '2026-11-16T23:21:00Z'
status: accepted
---
## Context

A Paperless workflow is a standing order: once it is enabled it runs by itself, with no further call from the model, on every document its triggers match (a scheduled trigger also on documents that exist already). Its actions can take things away (move the documents to the trash, remove tags, correspondents or permissions), send documents or text outward (an e-mail to any address, a POST request to any URL), or change who owns and may see documents. Decision-6 reserves `confirm` for destructive tools and decision-12 and decision-17 add the cases where a change cannot be undone by the caller. A workflow is such a case: creating or updating one looks like an edit of settings to a model, but its effect is repeated for as long as it exists, a mail that went out cannot be recalled, and Paperless's own trash is the only safety net for documents.

Probed on Paperless 3.4.2 (the workflow contract tests):

- Deleting a workflow leaves its triggers and actions behind as objects of their own (`/api/workflow_triggers/`, `/api/workflow_actions/`); they belong to no workflow and do nothing. An update that replaces a list does the same with the replaced objects.
- Paperless stores settings that belong to another type of action (a title on a "move to trash" action) without complaint, and accepts a regular expression that does not compile.
- A consumption trigger needs a path, file name or mail rule filter; remote OCR needs a consumption trigger in the same workflow.
- Password removal actions carry passwords and webhook actions carry request headers, which can hold tokens; the API returns both in clear.

## Decision

- `delete_workflow` takes `confirm: bool = False` and is destructive (decision-6): there is no trash for workflows. Its refusal names the workflow, how many triggers and actions are lost, and that no document is touched.
- `create_workflow` and `update_workflow` take `confirm: bool = False` too. They stay annotated non-destructive and open-world (a workflow can send documents out), and need `confirm` only when the workflow that results is **enabled** and one of its actions **acts**: move to trash, e-mail, webhook, a removal that removes anything, or an assignment of the owner or of view or change permissions, and also the actions this server cannot write (password removal, AI suggestions, any unknown type) that a workflow made in the web UI can hold, so that switching such a workflow on needs confirm. Plain assignments (title, tags, correspondent, document type, storage path, custom fields) and remote OCR need none. A workflow created or left disabled needs none either, and neither does a change of name or order alone.
- An update needs `confirm` when it replaces the triggers or the actions of such a workflow, or switches it on; switching it off, or leaving enabled state, triggers and actions alone, does not.
- The refusal uses `ConfirmationRequired.for_effect` (decision-15) and names the workflow, its triggers (type and which filters are set; for a scheduled trigger that it also reaches existing documents, or that there is none yet) and each acting action: for an e-mail the recipients and whether the document goes along, for a webhook the host only (a URL can carry credentials), for a removal what is removed.
- Only some types can be written (decision-4 keeps the tool set small): all four trigger types and the action types assignment, removal, e-mail, webhook, move to trash and remote OCR. Password removal (secrets this server must not show) and applying AI suggestions (a query to Paperless's AI backend for every matching document) are refused with an explicit error that says why and points to the web UI, as is any number or name that is no type. Both are still listed, by type, when read.
- A setting that does not belong to its trigger or action type is an error, not ignored; Paperless would store it silently.
- Webhook header values and stored passwords are never shown (`(hidden)`); the webhook URL is shown as stored, like Paperless does for every user who may view workflows, but a refusal names only its host. An update that replaces the actions sends a header value as `(hidden)` together with the id of the action to keep the stored value; without a stored value, or when the URL's scheme, host or port differs from the stored one (the credential would travel to another destination unseen), it is an error. Headers left out of a webhook are removed. Settings the tools do not know (a misspelt name) are an error, not dropped, and a taxonomy id is looked up like a name. An update that would replace actions on a workflow holding an action of a type this server cannot write is refused, so nothing is dropped silently (anti-goal, design section 2).
- This supplements decisions 6, 12 and 17 and changes none of their rules.

## Consequences

- Building an automation that acts is two calls by design: the model describes what will run unattended and gets the user's yes. Filing automations (tags, titles, correspondents) take one call.
- A client that never passes `confirm` can still create disabled workflows and filing workflows, but not switch on a workflow that deletes or sends.
- A webhook action makes Paperless, not this server, call the URL, so decision-7 (no host network access for remote callers) is not touched; Paperless's own limits on webhook ports and local addresses apply, and the refusal names the host so the user can judge it.
- The orphaned triggers and actions Paperless leaves behind are not removed by this server; they are inert.
- Names are the unit of lookup, so a workflow cannot share a name with another one, ignoring case, although Paperless allows only exactly equal names to clash.
- Tool descriptions and the refusal text are part of the contract; the surface snapshot shows them.

## Enforcement

- The registry test (tests/tools/test_registry.py) requires an optional `confirm: bool = False` on `create_workflow` and `update_workflow` (the acting tools) as well as on the destructive `delete_workflow`, with a self-test on a synthetic tool without one.
- Service tests (tests/services/test_workflows.py): the refusal text for every kind of acting action and for triggers, the cases that need no confirmation (disabled, plain assignment, rename, switch off), the unwritable types and settings, the hidden header rules and that the fake, created without `allow_writes`, sees no write on a refused call.
- Adapter tests against the vendored schema (tests/adapter/test_workflows.py), the contract suite on the fake and live 3.4.2 (tests/contract/test_workflows.py), tool tests (tests/tools/test_workflows.py) and E2E through the assembled server (tests/e2e/test_workflows.py).
