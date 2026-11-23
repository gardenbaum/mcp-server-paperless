---
id: PLMCP-76
title: Deliver files of any practical size, also to a directory
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:14:47Z'
updated_date: '2026-11-23T23:33:35Z'
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
- [x] #1 Over stdio, original, archive, versions and bulk ZIPs can be saved into explicitly allowed directories (off by default), with the path rules of PAPERLESS_MCP_UPLOAD_DIRS, without overwriting unless confirmed
- [x] #2 Files above the attach limit can be read in parts over both transports
- [x] #3 A new decision supersedes decision-7's rule 'downloads are never written to the host' for stdio with a configured directory allowlist only; HTTP never offers a path or writes to the host; it also says whether such a tool is read-only (PAPERLESS_MCP_READ_ONLY) and how it fits decision-11 (MEDIA_TOOLS)
- [ ] #4 A second decision keeps or changes the default attach limits, from measured client limits
- [x] #5 If a tool joins the default profile, a decision weighs it against decision-34: 19 of 20 tools are used, so one slot is free
- [x] #6 Security tests cover symlinks, traversal, hard links, dot files, races and full disks; docs, the plugin settings and the 'never written to disk' tool texts match
- [ ] #7 Contract tests on the fake and Paperless 3.5.1, tool tests and E2E cover every file kind, parts, overwrite with and without confirm and both transports
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
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

Probes on paperless-ngx 3.5.1 (2026-11-23): Range is ignored (200), answers are zstd-compressed without Content-Length; size and SHA-256 of the metadata match original and archive of a PDF, image and text, per version and after a reprocess; a version of another document is 404. ZIP: same bytes in four calls for 4 compressions x 3 contents on SQLite and PostgreSQL, whatever the order of the ids; after a metadata edit still the same on SQLite, on PostgreSQL 4 of 5 edits reordered documents of the same created date. Reported to the main agent, who decided for stateless parts (decision-45).

Real clients (C1), still to run before decision-46 is accepted and criterion 4 is checked. Claude Code: the main agent. Cursor and Claude Desktop: the owner.
Preparation: a throwaway Paperless (just e2e-up, http://127.0.0.1:8010, admin / e2e-admin-password), never the real one. Upload one PDF or image file of about 100 KiB, 512 KiB (at most 524288 bytes), 760 KiB, 1 MiB, 8 MiB and 16 MiB and note the document ids. Make an empty directory, e.g. ~/paperless-downloads.
A. stdio (Cursor, Claude Desktop, Claude Code): add a second, temporary server entry that runs this branch, command 'uv', arguments 'run --directory <worktree> mcp-server-paperless', environment PAPERLESS_URL, PAPERLESS_TOKEN, PAPERLESS_MCP_TOOLSETS=core,admin, PAPERLESS_MCP_MAX_DOWNLOAD_BYTES=33554432, PAPERLESS_MCP_DOWNLOAD_DIRS=<that directory>. Restart the client, remove the entry afterwards.
B. HTTP (Claude Code, Cursor): the same environment for 'uv run mcp-server-paperless-http' plus PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK=true, client URL http://127.0.0.1:8000/mcp.
For each file say: 'Download document <id> with download_document and tell me its size.' Note: worked, error text, cut off, saved to a file by the client, or the model did not see the file. Stop at the first size that fails.
Then, once per client: (1) set PAPERLESS_MCP_MAX_DOWNLOAD_BYTES back to its default and say 'Read document <8 MiB id> in parts with offset and give me the first two parts and their checksums'; note whether the model repeats the arguments of next and whether it can join two parts. (2) stdio only: 'Save document <8 MiB id> to <directory>', then the same again (it must ask before it replaces), then 'Save it to <another directory>' (refused). Compare the file with Paperless (shasum -a 256). (3) Note whether the client asks for approval before each download call (expected with download directories: the tools are not read-only then).

Evidence. AC 1: tests/services/test_file_sink.py, test_file_saving.py, tests/tools/test_downloads.py; live over stdio tests/e2e/test_downloads.py. A ZIP has no versions in Paperless: versions are saved per document, a ZIP holds the newest. AC 2: tests/services/test_file_parts.py, tests/tools/test_downloads.py, test_downloads_transport.py (stdio, HTTP legacy and auto). AC 3: decision-45, the variant matrix in test_downloads.py, test_registry.py. AC 4: open, decision-46 is proposed until the real clients are measured. AC 5: no tool joins the default profile (20 of 20, budget test unchanged), so no budget decision. AC 6: test_file_sink.py on macOS and in a Linux container with a really full tmpfs (CI mounts one); exFAT, FAT32 and HFS+ images on macOS: save, confirmed replacement and a full disk. AC 7: open until the hosted run of the final commit; locally on 3.5.1 the contract modules (99 passed) and tests/e2e/test_downloads.py (4 passed) were green before the last changes (tests only).
Security review findings (2026-11-23): (1) applied: the ending came from the proposed name, which is the title's where Paperless knows no ending for a type, or the upload's where no name is proposed; now a fixed table of the types Paperless 3.5.1 consumes decides, .bin for others. (2) applied: the reserve is checked before the file is made and before every piece. From the PLMCP-98 review: confirmation texts quote the whole file name and directory, and a replacement that cannot be shown exactly is refused.
More probes: ZIP of 320 MiB: first byte after 0.8 s (none) and 147.6 s (lzma, so a lzma part of that size exceeds the 120 s for a part; saving has 600 s); saved in 6.2 s; Paperless killed in mid-body, zstd and identity: an error, never a short file.
Mutation (mutmut in a Linux container, changed modules, first run, before the last tests): 91 survivors outside the reviewed equivalents; tests were added for those that mattered (a temporary file swapped for a link to itself, the facts of a named version, the wait of a part, texts). The second run was stopped with the local containers on the owner's request, so the effect of the added tests is not measured. Found on the way, not from this task: the mutation run cannot collect its statistics since tests/adapter/test_schema_guard.py::test_a_request_is_recorded_with_the_port_method_that_sent_it (mutmut renames the method; excluded in that container only), and three entries of mutation-equivalents.toml are stale (add_note 11, delete_taxonomy_item 7, merge_as_versions 20).
Deviations from the design: open_archive takes wait_seconds (the two waits need it); parts and saves have report models of their own, so the report of an attached file only gains version_id; endings from a table (security review); a document's metadata is read again after a part; the files are opened O_RDWR (the end record of a ZIP is read back); CI mounts a small tmpfs. Deliberate test changes: surface snapshot (two tools), the notes over the limit, the thumbnail report's version_id, the registry's path allowlist, the import contract's two new modules.

Correction: the first mutation run left 96 survivors and 5 timeouts outside the entries of mutation-equivalents.toml (the moved entries included), not 91.

Independent review (2026-11-24, 99b905c), all verified by a failing test first. I1 ZIP parts took any 200: a part and a save now need Content-Type application/zip (Paperless 3.5.1 sends exactly that: BulkDownloadView, and measured on the E2E stack), a member at byte 0 and an end record that counts to the last byte; the claim that a body never ends early is corrected for bodies that end with their connection. I2 a name with U+200C before .git is .git on HFS+: every component below an allowed directory with a control, format or break character is refused, in allowed_paths, so uploads by path are covered too; that hole existed before this task (read_allowed). A consequence: files whose name has such characters (a zero-width joiner in an emoji) can no longer be uploaded by path. I3 a close that fails no longer skips the unlink or keeps the directory open. M1 reading lets others run once per decoded mebibyte; no limiter for parts and saves was added: the only one in the code is the private semaphore of PageService, which is for the renderer's memory. M2 with I1. M3 a proposed name is cleaned instead of refused. M4 a streamed POST that reads has no 'may have been applied'. M5, M6 texts. M7 a temporary name that stays after the hard link is reported. M8 left to PLMCP-103 (main agent).

Follow-up to the review: reading by path is no stricter than needed. A dot name counts as one behind format characters, control and line-break characters are refused, and other format characters (the joiner in an emoji) are read as before; this replaces the consequence named under I2. Saving stays strict. The ZIP that bulk_download attaches whole has the same check of its start and end (its port does not hand over the Content-Type).
<!-- SECTION:NOTES:END -->
