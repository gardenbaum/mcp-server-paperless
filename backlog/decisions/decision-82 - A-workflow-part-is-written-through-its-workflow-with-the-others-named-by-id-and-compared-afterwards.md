---
id: decision-82
title: >-
  A workflow part is written through its workflow, with the others named by id,
  and compared afterwards
date: '2026-11-25T07:31:41Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-87 AC 2 and 4: one trigger or action is added, changed or removed without resending the others, and every path keeps decision-21. `update_workflow` replaces whole lists: an action with hidden values has to be written back by its id, and a workflow that holds a type this server does not write is refused. The single endpoints of Paperless know no workflow and fail for e-mails and webhooks (decision-81, P2).

P1 (decision-81) showed that a PATCH of a workflow keeps the parts it names by their ids. On 2026-11-24 the owner approved the design with its recommended options, "Alle wie empfohlen" (all as recommended), in a question dialog quoted in decision-81: here the numbers 1 (through the workflow), 6 (a yes for hidden values, for both tools), 9 (guide limit), 10 (a shared part is not changed) and 12 (the type does not change).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **`update_workflow_part`** adds, changes (settings, `clear`, `position` of an action) or removes one trigger or action. Triggers and actions are numbered apart in Paperless, so the part is named by `trigger_id` or `action_id`, as in `get_workflow`. It is destructive, open world and not idempotent.
- **One PATCH of one list.** Other actions are sent by id only: what is hidden, or of a type this server does not write, never leaves Paperless. Other triggers are sent back as read, with their ids, because Paperless makes an unfiltered consumption trigger of an id that is gone and does not look up the type of a trigger sent by its id alone. A changed action sends its id and what changes; e-mail and webhook are given whole.
- **Read again right before the PATCH.** If triggers, actions or the enabled state differ from what was read and confirmed (a part added, changed or removed by someone else), the call is refused and nothing is sent. Between this read and the PATCH, milliseconds, a change is still lost (Consequences).
- **Values of custom fields are merged**: `assign_custom_fields_values` of a change sets the named values, the other stored ones stay, and the result says how many. Values of fields that are not (or no longer) in `assign_custom_fields` are dropped from what is written, so that none comes back unseen with its field; that is also how a value is removed.
- **Checked here, because Paperless does not**: the part is in the workflow; a part that another workflow holds is not changed (it can be removed); actions are not rewritten while one that stays is held by another workflow with several actions, whose order could change; the type stays; a consumption trigger keeps a path, file name or mail rule filter; a blank text is refused as a filter (Paperless trims it to none), in `create_workflow` and `update_workflow` too; a match that a change touches needs its algorithm, and one without is not called a filter; doubles in a list are sent once; remote OCR and AI suggestions keep the trigger they need, each rule on its own. Types this server does not write can be moved and removed.
- **Compared afterwards** with a fresh read of the workflow (the answer of the PATCH does not have the actions in their order): the parts that stay, the one that changed, the new one, the order of actions, and that nothing else is there. Spaces around texts, empty values and the order inside lists do not count; header values, webhook parameters and custom field values count exactly. A difference is an error that names parts and settings, never values, and says not to call again. A lost answer is looked up once the same way, and after a 400 the workflow is compared with what it was (Paperless checks e-mails and webhooks once more while it writes). Nothing is ever sent again.
- **Confirmation as in decision-21**, for every operation that leaves an enabled workflow that acts; the text names operation and part first. Bound are the workflow as read and the planned parts, hidden values included. The credential and copy rules of decision-66 hold.
- **Hidden values that are lost need a yes, also while the workflow is disabled**: webhook header values, credentials in a webhook URL, passwords; in `update_workflow_part` and, by replacing the actions, in `update_workflow`. The text names the action and header names, not the values. If another workflow keeps the action, nothing is lost. Where decision-66 already says what becomes of the credentials of an action, they are not named twice; those of every other action are.
- On 2026-11-25 the owner approved three consequences as built, with "Überall wie von dir empfohlen bitte." (everywhere as you recommend, please): hidden values that are lost are asked about on disabled workflows too; actions shared with another workflow are not rewritten (the order rule); a stored trigger Paperless no longer accepts blocks writing other triggers until it is repaired or removed.
- Supplements decisions 21, 44 and 66; their rules are unchanged except that `update_workflow` asks in more cases.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Between the second read and the PATCH (milliseconds, Paperless has no conditional write) another user's change or addition in the same list is written over without notice, and an action removed in that time comes back as an empty assignment, which the comparison reports. `update_workflow` replaces whole lists and does not read again.
- A change costs three reads (all workflows, the workflow before and after) and one write.
- An enabled workflow that holds a destination that cannot be shown, or too many, is not changed part by part, not even to remove another part; the error says to switch it off first.
- A rule Paperless normalises in a way the comparison does not know would be reported as a difference although all is well; the contract tests show those that are known.
- Replacing a webhook header by a new value asks, too: the old value cannot be brought back.
- A stored trigger Paperless no longer accepts (a custom field query with a deleted field) has to be repaired or removed before another trigger is written; removing it works.
- The mechanism is not documented by Paperless: `tests/e2e/test_workflow_parts.py` and the contract tests guard it, also on the weekly run against the latest release.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

- Resending every part in full, stored secrets included: new ids, secrets travel, parts of unknown types too.
- The single endpoints: see decision-81, P2.

## Enforcement

- `tests/services/test_workflow_parts.py` (marker decision-82): every operation, refusal, confirmation and outcome, and as a property that no operation touches or sends another action. `tests/services/test_workflow_credentials.py`: hidden values lost by `update_workflow`.
- `tests/adapter/test_workflows.py`, `tests/contract/test_workflows.py` on the fake and Paperless 3.5.1, `tests/tools/test_workflows.py`, `tests/e2e/test_workflows.py`.
