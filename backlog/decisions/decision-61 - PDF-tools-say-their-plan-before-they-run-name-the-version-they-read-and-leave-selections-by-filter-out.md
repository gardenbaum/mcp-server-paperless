---
id: decision-61
title: >-
  PDF tools say their plan before they run, name the version they read, and
  leave selections by filter out
date: '2026-11-24T09:17:34Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The PDF tools of paperless-ngx 3.5.1 read the newest file of a document by default, hold the pages of an edit against the page count of the row that is sent (for a document its first file), and offer options no tool sent. AC 4 of PLMCP-79 asks that every operation says before it runs what it produces and what is lost.

The design was put to the owner as 14 decisions with recommendations. On 2026-11-24 the owner approved all of them: "Bitte alles wie von dir empfohlen umsetzen." (please do everything as you recommend) (PLMCP-79). That includes leaving out `all`, `filters` and `excluded_documents` of rotate and reprocess (decision 6a), because one call could then act on documents nobody was shown, and `delete_original` of edit_pdf and remove_password and `delete_originals` of merge (decision 3a, see decision-60).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Plan.** Every writing pdf tool has `dry_run`, which checks everything, starts nothing and returns `state: not_started` with a `plan` (what is produced, kept and lost, and whether confirm is needed). A real run returns the same plan. Supplements decision-4.
- **Version.** Edits and unlocking always name the version whose file is read (`documents: [version id]`, `source_mode: explicit_selection`), so pages are checked by both sides against the same file. `version_id` chooses an older one, which the document must list; `use_original_files` (rotate, merge) takes the first file. A merge takes no version id: its metadata would come from the version's row, which has none.
- **Going back** to an older file as the newest version needs confirm, although nothing is lost: the document then shows other content. rotate_documents takes confirm for that case only and stays marked as not destructive. Supplements decision-6.
- **Metadata.** `keep_metadata=false` sends `include_metadata: false`; with `new_version` it is refused, because a version has no metadata.
- **Merge.** What Paperless would leave out without a word is refused before: an encrypted or unreadable PDF, another file without `archive_fallback`, or without an archive PDF.
- **Excluded** fields, owner-approved as quoted above: `all`, `filters`, `excluded_documents` of rotate and reprocess; `delete_original` of edit_pdf and remove_password; `delete_originals` of merge.
- **Confirmations** bind the page count, the newest version, the version that is read, `keep_metadata`, and, where originals go, their archive serial numbers.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- About 1 KB more definition per tool of the opt-in toolset; the default profile is unchanged.
- The file type is taken from the file that is read, so a newest version that is no PDF is refused instead of being skipped by Paperless.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Any version everywhere, with restricted metadata documents: not taken (decision 10 of the design).
- Filters resolved to ids here: comes with PLMCP-80 as a shown list of ids.
<!-- SECTION:ALTERNATIVES:END -->


## Enforcement

- `tests/tools/test_registry.py`: every pdf tool has `dry_run` and a plan; rotate takes confirm and is not destructive.
- `tests/services/test_pdf_plans.py`: previews send nothing, the plans, versions, refusals and confirmations. `tests/services/test_confirmation_scope.py`: what they bind.
- `tests/contract/test_pdf.py`: pages, versions and first files on the fake and on Paperless 3.5.1.
