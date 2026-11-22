---
id: PLMCP-30
title: 'Toolset workflows: workflows with triggers and actions'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-17T00:27:00Z'
labels: []
milestone: m-4
dependencies:
  - PLMCP-15
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 30000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Workflows automate filing in Paperless; triggers and actions are nested objects with many types (design §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Workflows list, get, create, update and delete (confirm), with triggers and actions nested and taxonomy by name
- [x] #2 Unsupported trigger or action types are rejected with an explicit error
- [x] #3 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [x] #4 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Domain (domain/workflows.py) and ports WorkflowReader/WorkflowWriter: a workflow is name, order, enabled, triggers and actions; each trigger/action is its numeric type plus its settings under Paperless's names (no secrets-leaking fields invented).
2. Adapter paperless/workflows.py on /api/workflows/ (nested triggers and actions sent without ids: an update replaces both lists), adapter tests schema-guarded.
3. FakePaperless (tests/fakes/workflows.py) + contract suite on fake and live 3.4.2 (also pins the nested PATCH/DELETE behaviour of 3.4.2 and orphaned triggers/actions).
4. Service WorkflowService: find by name or id, taxonomy by name (tags, correspondents, document types, storage paths, custom fields, users, groups) in triggers and actions, table-driven; trigger types 1-4 and action types 1-4, 6, 7 supported for writes; password removal (5, secrets), apply AI suggestions (8) and unknown numbers rejected with an explicit error naming the type; an update that would drop an action or trigger of an unsupported type is refused instead of dropping it silently; webhook header values and PDF passwords are never echoed (a hidden header value keeps the stored value when the action is sent back with its id).
5. Confirm (new decision-18): delete always; create/update need confirm when the resulting workflow is enabled and acts outward or takes things away (e-mail, webhook, move to trash, removal, owner/permission assignment); refusal via ConfirmationRequired.for_effect naming the actions and triggers.
6. Tools (tools/workflows.py, toolset workflows, opt-in): list/get/create/update/delete_workflow; central annotations, TOOL_TOOLSETS, registry check for the acting tools, import-linter independence, surface snapshot, visibility tests, server wiring, README.
7. E2E on live 3.4.2 under the lock; mutation on services.workflows and paperless.workflows; reviewer; squash.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented bottom-up, red first at each level: domain tables checked against the vendored schema (tests/domain/test_workflows.py), adapter (schema-guarded), FakePaperless + contract suite (fake and live 3.4.2), service, tools, E2E. New decision-21 (id 21 because Backlog numbers across branches) records the confirm rule.

Probed on live 3.4.2 through the contract tests: names are unique and not blank; a consumption trigger needs a path, file name or mail rule; remote OCR needs a consumption trigger in the workflow; Paperless stores settings of another action type silently and accepts a bad regex; deleting a workflow, or replacing a nested list, leaves the old triggers and actions as orphan objects under /api/workflow_triggers/ and /api/workflow_actions/ (the live contract cleanup now deletes unreferenced ones). Webhook headers and passwords come back in clear, so the service shows '(hidden)'.

Local gates: just check passes except the gitleaks history scan, which fails on tests/adapter/test_admin.py (commit 7108022, branch plmcp-31-admin, not on this branch). 100% diff coverage. Mutation on services.workflows and paperless.workflows: 84 survivors, all reviewed and recorded in mutation-equivalents.toml (71 typing.cast literals, httpx2 method case, unreachable defaults in _show and _action_setting). Live 3.4.2 under the lock: contract/test_workflows, e2e/test_workflows, e2e/test_stdio, 25 passed. Surface snapshot: additive only (5 tools in all, 2 in read-only); schema guard: new NULL_OBJECT_PROPERTIES exemption (null email/webhook, nullable allOf) with tests. Not written red first: the registry ACTING list and its self-test (the tools already had confirm). No independent reviewer was run (no subagent tool available to this session). DoD 2 left unchecked (CI not run), DoD 4 unchecked (not merged).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Opt-in workflows toolset: list_workflows, get_workflow, create_workflow, update_workflow, delete_workflow with nested triggers and actions and taxonomy by name; unsupported or unknown trigger/action types and settings of the wrong type are explicit errors; password removal and AI-suggestion actions are not writable and never dropped silently; webhook headers and passwords never shown. decision-21: delete always needs confirm; create/update need confirm when the resulting workflow is enabled and trashes, e-mails, calls a webhook, removes values or changes owner/permissions. Default profile unchanged. Verified by unit, adapter (schema-guarded), contract (fake and live 3.4.2) and E2E tests.
<!-- SECTION:FINAL_SUMMARY:END -->
