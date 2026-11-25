---
id: decision-91
title: >-
  Saved views: display settings are checked here, grants go through
  set_permissions, PUT and list options stay out
date: '2026-11-25T10:26:06Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-88 AC 1, 3 and 4: every field of a saved view can be read and written, sharing follows decisions 12 and 19, and every endpoint of saved_views is covered or left out with the owner's approval.

On 2026-11-24 the owner approved the design: asked in a question dialog "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?" (do you approve the drafts with all recommended options, with the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010), the owner chose "Alle wie empfohlen" (all as recommended). So the omissions named as decisions 1 and 2 of the draft are owner-approved exclusions of 2026-11-24, and decisions 7 (the icon is a text), 9 (no grants in the list) and 10 (three parts) are approved.

Probed on Paperless 3.5.1 (CI run 45594222648, job 139135939753 "e2e (1/6)", temporary tests of PLMCP-88.1):

- **P1** UI settings sent back without what Paperless computes are the same on the next read, nested values, large numbers and odd ids included. `saved_views` can hold anything. A POST without `settings` makes every following GET a 500. See the decision of PLMCP-88.2.
- **P2** With `full_perms` whoever sees a view sees its grants. A change grant carries a view grant. Grants and owner are changed by the owner, a superuser or, of a view without owner, anyone who may change views; a user with a change grant gets a 403 for them, but may change the other settings. `id__in` is ignored by the list. Handing a view to a user who has one of that name is a 400, after the new grants of the same request were stored.
- **P3** Reading UI settings needs `view_uisettings`, writing `add_uisettings`; `change_uisettings` does not count.
- **P4** An empty list of display fields stays on POST and becomes null on PATCH. Duplicates, `xcustom_field_3`, `custom_field_03` and objects are stored; `custom_field_` without a number and numbers are a 500. A custom field need only exist, not be visible. Deleting a custom field takes it out of the views that show it. `display_mode` takes an empty text.
- **P5** (read in the source, v3.5.1, `saved-view.service.ts`, not run) The dashboard and the sidebar show every view the user sees whose id is in the lists, whoever owns it.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **`icon`, `display_mode`, `display_fields`** are shown by every tool that returns a view, and set by `create_saved_view` and `update_saved_view`; the last two can be cleared (`clear_display_mode`, `clear_display_fields`). As for the other settings no confirmation is asked (decision-14).
- **Checked here before anything is sent**: the icon is one of the 57 of 3.5.1 (the error lists them; an enum would cost 0.6 KB in each of two schemas), display fields are 1 to 50, each once, one of the 12 names or exactly `custom_field_<id>` of a custom field the user can see. Stored fields are not checked when something else changes.
- **Shown as stored**: a custom field with its name where it can be read; what is no list is shown as null.
- **Owner and grants go through `set_permissions`** with the kind `saved_view`, by name or id (decisions 12 and 19 hold unchanged; supplements 19). They are read view by view, because the list ignores `id__in`. `get_saved_view` shows them, `list_saved_views` does not (one request per view).
- **Left out, owner-approved as quoted above (2026-11-24)**: `PUT /api/saved_views/{id}/` (PATCH does it); `ordering` of the list (only by name, and the list is whole and by name) and its `full_perms` (grants are read per view); `owner` and `set_permissions` on POST (create, then set).
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- An icon or display field of a later Paperless is refused until this server knows it (the domain test compares the icons with the vendored schema).
- A custom field the user cannot see cannot be set, although Paperless would take it.
- Sharing a new view takes two calls.
<!-- SECTION:CONSEQUENCES:END -->


## Enforcement

- `tests/domain/test_views.py`: icons and display modes against the vendored schema, the form of a custom field. `tests/services/test_views.py` (marker decision-91): every setting, clearing, refusals before anything is sent.
- `tests/adapter/test_views.py`, `tests/contract/test_views.py` on the fake and Paperless 3.5.1, `tests/tools/test_views.py`, `tests/e2e/test_views.py`.
- `tests/quality/test_api_coverage.py`: each exclusion needs the quote and the date from this file.
