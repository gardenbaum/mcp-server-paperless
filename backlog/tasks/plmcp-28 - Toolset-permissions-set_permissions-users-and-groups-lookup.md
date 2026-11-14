---
id: PLMCP-28
title: 'Toolset permissions: set_permissions, users and groups lookup'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-14T03:15:00Z'
labels: []
milestone: m-3
dependencies:
  - PLMCP-9
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 28000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Owners and view/change permissions decide who sees what; a typo must not wipe permissions, as one reference server guards against with strict objects (design §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 set_permissions works on documents and taxonomy items: owner and view/change users and groups, by name or id, in merge or replace mode
- [ ] #2 Replace mode and owner changes require confirm; unknown users or groups fail before anything is sent
- [ ] #3 list_users and list_groups return compact rows
- [ ] #4 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [ ] #5 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe 3.4.2: PATCH set_permissions/owner on documents and taxonomy (merge vs replace, omitted parts, full_perms), /api/users and /api/groups for a non-superuser, owner rules.
2. Domain: domain/permissions.py (rows, request, results), ports PermissionReader (get_permissions, list_users, list_groups) and PermissionWriter (set_permissions), reusing ObjectPermissions from domain/bulk.
3. Adapter paperless/permissions.py (schema-guarded adapter tests with recorded 3.4.2 responses), FakePaperless + contract suite on fake and live.
4. Service services/permissions.py: PermissionService (merge/replace computed client-side on the read grants, confirm for replace and owner changes naming gainers and losers, principals resolved before anything is sent); shared principal resolution extracted to services/principals.py and reused by bulk_edit_taxonomy.
5. Tools tools/permissions.py (opt-in toolset permissions: set_permissions, list_users, list_groups), registry/annotations, surface snapshot, README, import-linter contract.
6. E2E on seeded data; just check, just mutation, just e2e.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Probed on Paperless 3.4.2 (shared E2E stack): (1) PATCH with set_permissions replaces, per action (view/change) and per kind (users/groups), the lists it carries and KEEPS lists it omits; an empty list clears; a change grant also adds the view grant but replacing the view list does not bring back a view grant a change grant had. Unknown user/group = 400 for the whole request ('Some users in don't exist or were specified twice.'), unknown action = 400. PATCH owner null removes the owner. There is no merge on the object endpoints, so merge is computed here: read the grants (list with id__in + full_perms=true; documents also fields=id,title,owner,permissions), compute the complete four lists, send all four (never relying on omitted-list behaviour). Bulk endpoints (documents/bulk_edit and bulk_edit_objects with merge) were not used: one PATCH path for documents and taxonomy gives the exact before/after needed for the confirmation text. (2) Only the owner (anyone for an unowned object) or a superuser may change permissions or owner: a user with a change grant and change_* permission gets 403 'Insufficient permissions.'; without the global change permission 403 'You do not have permission...'. Any user who can see an object sees its grants with full_perms=true. Hidden objects are simply absent from the id__in list. (3) /api/users/ and /api/groups/: 403 without view_user/view_group; with it ALL users are listed (also for non-superusers), full rows incl. e-mail and permission lists, the fields= parameter is ignored; so the compact rows are built in the adapter (id, username, first+last name, is_active, is_superuser, group ids; groups: id, name). Filters: username__icontains, name__icontains.

Decisions: toolset permissions (opt-in, default profile stays at 18 tools): set_permissions(kind, items, owner|clear_owner, view_users/view_groups/change_users/change_groups, mode merge|replace, confirm), list_users, list_groups. Parameter named items (not document_ids) because one tool covers documents (ids) and four taxonomy kinds (names or ids), as bulk_edit_taxonomy/delete_taxonomy_item do. Lists left out (null) are never touched in either mode; replace replaces only the lists given, so a forgotten list cannot wipe anything, an explicit [] clears that list. A change grant includes view: a changer always keeps view (replace change_users to take change away). Merge only adds and needs no confirm; replace needs confirm whenever it would change something; any owner change or clear needs confirm in both modes (decision-12) and a no-op (already as asked) needs none and sends nothing (same precedent as decision-12). The refusal (ConfirmationRequired.for_effect, decision-15) lists per object the owner change and who gains view/change and who loses change/view/all. Unknown/ambiguous users and groups, unknown objects and documents named by title fail before anything is sent (TaxonomyResolver for users/groups; shared resolve_principals extracted from bulk_taxonomy into services/principals.py and reused there; a non-lister may pass ids, Paperless then rejects unknown ones with 400 before changing). Objects are PATCHed one at a time; if one fails the error says which were already changed. Overlap: bulk_edit_taxonomy (bulk toolset) keeps its set_permissions operation (changing the accepted bulk contract was out of scope): it is taxonomy-only, always needs confirm, has dry_run and replaces by default; set_permissions also covers documents, merges by default and confirms only when risky; both descriptions/README say so. update_document(owner=) stays for owner changes alongside other document edits (same confirm rule). list_users/list_groups vs list_taxonomy(kind=user|group): add superuser/active/group-membership flags, paging and server-side filter; descriptions say so. Schema guard: PATCH full_perms declared as undeclared-but-honoured on the five object endpoints, and responses to full_perms=true requests get the schema-required user_can_change filled in before validation (3.4.2 answers permissions instead) - deliberate, documented in tests/adapter/schema_guard.py. Surface snapshot regenerated deliberately (new toolset only).

Rebased onto 4b4101d (sharing, decision-17: publishing share links needs confirm, alongside decision-12 for owner changes; the two do not overlap). Gates: just check 100% diff coverage (gitleaks history flags 4 test passwords from the plmcp-29-mail branch of another worktree, not in this branch); just mutation 100.0% (126 reviewed equivalents, one added: PATCH method case in paperless/permissions); full e2e 493 passed, 6 skipped, 1 failed = known PLMCP-40.
<!-- SECTION:NOTES:END -->
