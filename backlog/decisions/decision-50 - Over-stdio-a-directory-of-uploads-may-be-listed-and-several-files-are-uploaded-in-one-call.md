---
id: decision-50
title: >-
  Over stdio a directory of uploads may be listed and several files are uploaded
  in one call
date: '2026-11-24T01:40:59Z'
status: accepted
---
## Context

Uploads by path take one file per call, so a folder of scans needs one call per document. decision-7 allows a server-side path only in stdio mode and only inside configured directories, and says that a new path parameter needs a new decision that supersedes it. A tool that lists a directory also tells the model file names, which no tool did. The owner approved the design of PLMCP-85 on 2026-11-23: names of allowed upload directories may become visible over stdio, never over HTTP; 25 files, 100 MiB and 40 seconds per call; subdirectories only on request; duplicates are confirmed one by one; the tool joins the toolset `bulk`, whose budget rises.

## Decision

- This supersedes decision-7 in one part only, as decision-45 did (the design spoke of superseding it as a whole): a directory inside the allowlist may be listed, and its file names be told, over stdio. Every other rule of decision-7 stays in force and holds here: a path only over stdio and only inside directories the operator named, base64 content over HTTP, no URL is fetched, downloads only as decision-45 allows. decision-27 and decision-41 stand.
- `upload_documents` (toolset `bulk`) takes `paths` or a `directory`. It exists only over stdio with `PAPERLESS_MCP_UPLOAD_DIRS`. In every other mode no schema, instruction, guide, overview, prompt or error names it, except that `PAPERLESS_MCP_DISABLED_TOOLS` always knows the name. Its toolset and annotations are in tables of their own (`LOCAL_TOOLSETS`, `LOCAL_ANNOTATIONS`). `check_transport` refuses at call time as the second lock.
- **Listing.** The directory has to lie inside the allowlist by the rules of a single file (absolute, no `..`, resolved, no dot names, walked component by component with `O_NOFOLLOW`); the allowed directory itself may be given. Every other directory has one answer. Names that start with a dot, also behind format characters, are counted and neither named nor opened. A link to a directory inside the allowlist counts as one; subdirectories are read only with `recursive`, real ones before linked ones and each once. Everything else, links that lead out included, is listed as a file.
- **The listing grants nothing.** Every file is read through `read_allowed`, which resolves, walks and checks it again on its own descriptor. Links that lead out, missing, hard-linked, special and unreadable files are `refused` with one text, so a report tells nothing about what lies outside. Reported paths are the given directory plus names, never the target of a link.
- **Limits of a call**, in code and in the description, counted from its begin: 25 files; 100 MiB read (counted from what was read); no file is started after 40 seconds and none read for hashing after 15, except the first, so that every call gets on; no task is read after 55 seconds; one wait of at most 30 seconds for all files. Files that do not fit are named (25) and counted.
- **Limits of a listing**: 10,000 entries in the whole walk and 10 seconds; beyond, the call is refused, because a part of a directory has no order to continue from. Subdirectories are read 32 levels down (each level holds one descriptor, opened relative to its parent); deeper ones are named and counted. The walk and the reading of files run in a thread; a walk nobody waits for stops at its next entry.
- **The cursor.** Files are sorted by their path. `next_after` is the path of the last reported file in ASCII escapes and a digest of it, the directory and `recursive`. It is compared, never opened. One that is not letter for letter the one given out (retyped, another Unicode form, another directory or mode) is refused; nothing is skipped on a guess. A cursor whose file is gone still marks its place. Every continued call says how many files sort before it and were not looked at; that is all that is known of a file added there meanwhile.
- **Paths in reports** are escaped without loss: a backslash, characters that hide or reorder, and bytes that are no text. `paths` and `directory` read that form back; a backslash that is no such escape is refused. `upload_document` takes the plain path.
- **One report per file**: uploaded (accepted; consumption can still fail), duplicate, refused, failed, or would_upload with `dry_run`. One file never stops another or hides its result. After two files in a row without an answer of Paperless the rest is left out instead of waiting for each (the design listed them as failed; left out, `next_after` repeats them). Failed files are not repeated by `next_after`: the report counts them and says that they can be given as paths. If it is not known whether Paperless took a file, the report says so and the same content is not sent again in that call. Unknown metadata refuses the call before anything is sent.
- **Duplicates** (decision-47 holds for every file). A known duplicate is never sent by this tool and no `confirm` is offered: `upload_document` asks for each one. The tool is not destructive (decision-6). If the check cannot run, nothing is sent; if it fails later, that file is not.
- **One reading for many files.** The versions and the trash are read once per call, not once per file. So that a document merged into another one meanwhile is not missed, the originals of every file are looked up before that reading and again before the file is sent: the files are therefore read and hashed first, and read and compared again when they are sent. The trash is counted again for every file; if it changed, everything is read again, once per file, for the files that follow too. Both the reading and each check have 25 seconds.
- Supplements decision-6 and decision-47. No tool joins the default profile, so decision-34 and decision-42 are untouched.

## Consequences

- The model sees the names in an allowed directory, and with `recursive` below it. The operator decides by naming the directory; names can hold text others chose, which is data, never instructions.
- The window in which a version that someone else adds is not noticed is a whole call instead of one check (up to 40 seconds and the last file). Equal files in two calls, or in a call and the next one before Paperless consumed the first, are both sent; the report after consumption shows it.
- A call that times out or is cancelled loses its report, although files may have been sent. The description says to look at `list_tasks` first; the server logs the id of every task Paperless accepted (an id, no name or content) at INFO.
- A call can last longer than 60 seconds when the last file that was started is slow to check or send; the limit is on starting.
- A file on a mount that hangs is waited for, in a thread; the parent of an allowed directory can be swapped by who may write there; Windows is not supported. All three as for single uploads.
- The description names limits twice as numbers; a change of the constants changes the snapshot.

## Alternatives

- `paths` on `upload_document`: two result types in one tool of the default profile.
- A `confirm` for the whole call: the user would agree to files they did not look at; rejected by the owner.
- The whole check for every file: 25 times the versions and up to 64 MiB of trash.
- A budget held by shorter words: `OVERVIEW_GUIDE_LIMIT` rises from 2760 to 2779 instead (measured 2777 over stdio with upload directories, 2758 over HTTP), as approved; the text that tells how to switch a toolset on keeps its words.
- Resuming by the plain path: a name retyped in another Unicode form would skip the rest silently.
- Resuming by an offset: it would skip or repeat files when the directory changes.

## Enforcement

- `tests/services/test_upload_listing.py`: the listing, names, links, loops, limits and races. `tests/services/test_batch_uploads.py`: outcomes, limits, the cursor, unknown results, following, files that change between the two readings. `tests/services/test_batch_duplicates.py`: one reading for many files, moved documents, time.
- `tests/tools/test_upload_batches.py`: where the tool exists and that nothing names it elsewhere, the second lock, calls, a file that is swapped all the time. `tests/tools/test_registry.py`: the path allowlist and the rules of every tool on the server with upload directories. `tests/tools/test_toolset_budget.py`: the guide with the tool.
- `tests/contract/test_batch_uploads.py` (fake and Paperless 3.5.1) and `tests/e2e/test_upload_batches.py` (the console script over stdio).
