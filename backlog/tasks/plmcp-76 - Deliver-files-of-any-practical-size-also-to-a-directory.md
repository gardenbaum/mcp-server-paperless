---
id: PLMCP-76
title: Deliver files of any practical size, also to a directory
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:47Z'
updated_date: '2026-11-23T22:07:58Z'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 65000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
download_document attaches files up to 512 KiB by default; larger ones are only linked. bulk_download has the same limit. Uploads by path exist for stdio, downloads by path do not, because decision-7 says downloads are never written to the host. Page images are PLMCP-74; this task delivers files. Requested by the owner on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Over stdio, original, archive, versions and bulk ZIPs can be saved into explicitly allowed directories (off by default), with the path rules of PAPERLESS_MCP_UPLOAD_DIRS, without overwriting unless confirmed
- [ ] #2 Files above the attach limit can be read in parts over both transports
- [ ] #3 A new decision supersedes decision-7's rule 'downloads are never written to the host' for stdio with a configured directory allowlist only; HTTP never offers a path or writes to the host; it also says whether such a tool is read-only (PAPERLESS_MCP_READ_ONLY) and how it fits decision-11 (MEDIA_TOOLS)
- [ ] #4 A second decision keeps or changes the default attach limits, from measured client limits
- [ ] #5 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [ ] #6 Security tests cover symlinks, traversal, hard links, dot files, races and full disks; docs, the plugin settings and the 'never written to disk' tool texts match
- [ ] #7 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover every file kind, parts, overwrite with and without confirm and both transports
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
Follows the owner-approved design (design-plmcp-76.md). 1. Measure ZIP stability and the file facts on 3.5.1 (SQLite and PostgreSQL) before parts of a ZIP are promised. 2. Decisions 45 (saving) and 46 (attach limits). 3. client.stream, the ports open_document_file and open_archive, the fake, contract tests. 4. Parts of a document file and of a ZIP. 5. allowed_paths moved out of upload_source without a change of behaviour. 6. file_sink with its security tests. 7. Saving in the services, the setting PAPERLESS_MCP_DOWNLOAD_DIRS, the stdio variants of download_document and bulk_download, the registration matrix. 8. E2E over stdio and HTTP, docs, plugin, coverage.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-74: GET /api/documents/{id}/download/ takes version_id in the port (DocumentReader.get_document_file), sent as ?version=; the coverage row for query:version moved off this task. get_document_pages uses it. No tool delivers the file of a version yet, so AC 1 still covers versions. The core toolset has 20 of 20 tools since decision-42: a further default tool needs a removal or a new budget decision (AC 5 says 19 of 20).
<!-- SECTION:NOTES:END -->
