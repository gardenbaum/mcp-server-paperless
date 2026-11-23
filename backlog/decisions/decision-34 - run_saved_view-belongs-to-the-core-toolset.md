---
id: decision-34
title: run_saved_view belongs to the core toolset
date: '2026-11-23T01:10:14Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
<!-- SECTION:CONSEQUENCES:END -->


## Context

The approved design (doc-1, section 6) put `run_saved_view` with the other saved-view tools in the opt-in `views` toolset. The default profile (`core`, `ai`) still lists saved views through `list_taxonomy` (kind `saved_view`), so an agent sees the views but cannot run them. User feedback on 2026-11-23 named running saved views as the change that would help most. The default profile had 18 tools against its budget of 20.

## Decision

- `run_saved_view` belongs to `core`; the default profile offers it. It is read-only, so read-only mode keeps it.
- The tools that create, change, delete, list and read saved views (`list_saved_views`, `get_saved_view`, `create_saved_view`, `update_saved_view`, `delete_saved_view`) stay in `views`: editing views is rarer, and `list_taxonomy` already lists them by name and id for `run_saved_view`.
- The tool stays in `tools/views.py` beside the rule translation it shares; its tag comes from the central toolset table, as for every tool.
- The default profile grows to 19 tools; the budget of 20 is unchanged.

## Consequences

- An agent with the default profile can run every saved view the user may see, with the paging and rows of `search_documents`.
- One tool of room is left in the default budget. Adding a further default tool needs a decision that weighs it against this one.
- The `views` toolset has five tools; the README and the tool reference say so.

## Enforcement

- `tests/tools/test_views.py::test_the_default_profile_runs_saved_views_without_the_views_toolset`: the default profile offers `run_saved_view` and none of the other view tools.
- `tests/tools/test_views.py::test_the_views_toolset_offers_five_tools_and_read_only_mode_keeps_two`.
- `tests/tools/test_registry.py`: the tag matches the central table, and the default profile stays within the budget of 20.
- `tests/quality/test_docs.py`: the README's tool counts match the catalogue.
