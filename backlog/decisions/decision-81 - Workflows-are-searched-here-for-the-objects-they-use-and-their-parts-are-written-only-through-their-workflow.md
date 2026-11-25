---
id: decision-81
title: >-
  Workflows are searched here for the objects they use, and their parts are
  written only through their workflow
date: '2026-11-24T22:53:17Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-87 AC 1 and 5: single triggers and actions are readable, the workflows that use an object can be found, and every endpoint of workflows, workflow_triggers and workflow_actions is covered or left out with the owner's approval. Paperless cannot search workflows for what they use, and its single-object endpoints know no workflow.

On 2026-11-24 the owner approved the design: asked in a question dialog "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?" (do you approve the drafts with all recommended options, with the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010), the owner chose "Alle wie empfohlen" (all as recommended). So the omissions named as decisions 2 and 3 of the draft are owner-approved exclusions of 2026-11-24, and decision 11 (single parts are arguments of `get_workflow`) is approved.

Probed on Paperless 3.5.1 (CI run 45503786740, job 138833596559 "e2e (1/6)", temporary tests of PLMCP-87.1):

- **P1** A PATCH of a workflow that names triggers and actions by their ids only leaves them as they are (all five trigger types, all eight action types, webhook headers, URL credentials, passwords and AI settings included), also where one of them is sent with changed settings; triggers sent back in full with their ids stay, too. `tests/e2e/test_workflow_parts.py` keeps this; that moving, adding and removing keeps the others is tested there against a fresh read, because the answer of the PATCH does not list moved actions in their order (green on Paperless 3.5.1, full run 45507183680).
- **P2** PATCH and PUT of an action with e-mail or webhook data, and POST of one, answer 500. PATCH of a consumption trigger answers 400 unless its filter is sent again. DELETE takes a part out of its workflow without a check (204). Reading needs `view_workflowtrigger` or `view_workflowaction`; `view_workflow` and `change_workflow` do not include them.
- **P3** Parts without a workflow (made alone, or of a deleted workflow) stay until any workflow is updated, and are gone then.
- **P4** An id that is gone is created: a trigger sent by id only comes back as a consumption trigger without any filter, an action as an empty assignment. A trigger or action of another workflow is taken over and changed for both, with POST too for triggers (POST drops the ids of actions). A trigger's type can be changed; remote OCR is refused where the triggers are sent by id only.
- **P5** Manual triggers store every filter and source, also those that do nothing. The AI action is accepted without AI configured, also enabled; it needs a trigger other than consumption and at least one known field. `trigger_type` and `enabled` filter the list without duplicates.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **`list_workflows` filters**: `enabled` and `trigger_type` are asked of Paperless. `uses_kind` with `uses` (a tag, correspondent, document type, storage path, custom field, user, group or mail rule) is searched here, over every setting of every trigger and action that names such an object, whatever the part's type, and over the custom fields of custom field queries (by id, or by exact name as Paperless reads them). `used_in` names the part and the setting.
- **Not found, and said**: names inside templates (titles, e-mails, webhooks), and parent and child tags that come along. A name must be visible to the user; an id may be searched for even where its object is not visible, since only ids standing in workflows the user sees are matched. Where the user sees no object of the kind with that id (mail rules aside, which are only known by id), the answer says so, and that a field searched by its id is not found in queries that name it. A name made of digits is not taken for an id: the error gives the id.
- **`get_workflow` reads one trigger or action** (`trigger_id`, `action_id`) with the workflows that hold it. Without the permission for parts it is read out of the workflows, if these may be viewed; without the permission for workflows, or with more than can be listed, its holders are unknown (null). An id of a workflow is read alone, not looked up in the list.
- **Hidden values stay hidden**: parts go through the same function as workflows (decision-21, decision-66).
- **Left out, owner-approved as quoted above (2026-11-24)**: writing on `/api/workflow_triggers/` and `/api/workflow_actions/` (POST, PUT, PATCH, DELETE: P2, P3; the same is possible through the workflow); their list endpoints (a part that acts is in a workflow); `PUT /api/workflows/{id}/` (PATCH does it); the list filters `id` and `id__in` (`get_workflow`); ids of triggers and actions on POST (P4); `email.id` and `webhook.id` (these objects are replaced as a whole).
- Corrects the note of decision-21 that replaced parts stay behind as objects (true on 3.4.2; on 3.5.1 see P3). Its rules and those of decision-66 are unchanged.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The output of `get_workflow` has one field more (`part`), and for a part the fields of the workflow are null or empty. `list_workflows` has `used_in` and `note` more.
- A setting stored under a type that ignores it is reported as a use.
- Orphaned parts can be read by their id, but not listed.
- The search reads all workflows; more than 2,000 are an error, as before.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/services/test_workflow_usage.py` (marker decision-81): every setting that names an object, queries as a property, restricted users, hidden values. `tests/adapter/test_workflows.py`, `tests/tools/test_workflows.py`: requests and arguments.
- `tests/contract/test_workflows.py` on the fake and Paperless 3.5.1: filters, single parts with and without permissions, prune. `tests/e2e/test_workflows.py`: the assembled server. `tests/e2e/test_workflow_parts.py`: P1.
- `tests/quality/test_api_coverage.py`: each exclusion needs the quote and the date from this file.
