---
id: decision-45
title: >-
  Over stdio downloads can be saved into allowed directories, and files are read
  in parts over both transports
date: '2026-11-23T22:17:57Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
`download_document` attached files up to 512 KiB and `bulk_download` the same; larger files were only linked, and no tool delivered a version other than the newest. Uploads by path exist over stdio. decision-7 says that downloads are never written to the host, for every transport. The owner asked on 2026-11-23 for files of any practical size, also into a directory, and approved the design of PLMCP-76.

Measured on paperless-ngx 3.5.1: Paperless ignores `Range` (200 with the whole file) and compresses its answers, so `Content-Length` is missing. The SHA-256 and size of the metadata match the downloaded original and archive version of a PDF, an image and a text file, also for each version and after a reprocess. A bulk ZIP is byte-stable while nothing changes: four calls gave the same bytes for each of four compressions and three contents, on SQLite and on PostgreSQL, whatever the order of the ids. After an edit of a document in the ZIP it stayed stable on SQLite; on PostgreSQL, with documents of the same created date, 4 of 5 edits reordered the ties (Paperless sorts by `-created` alone) and so changed the bytes.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Parts, both transports.** `offset`, `max_bytes` and `checksum` on both tools, `version_id` on `download_document`. A part is at most the attach limit and the file at most 2 GiB. Nothing is kept between calls: each part streams the file from its start, a ZIP is built by Paperless and read and hashed to its end every time. A part after the first names the version and the checksum of the first (a ZIP: the checksum). If it differs now, or a document's metadata changed while the part was read, nothing is delivered and the caller starts again. A ZIP must say that it is one (`application/zip`) and be one from its first byte to its end record. A part of a document is not hashed against the whole: a body that ends with its connection can be short, and a file can change after the last look, so `next` and the description tell the reader to compare the SHA-256 of the whole with `checksum` at the end. That comparison, not the server, proves the parts. On PostgreSQL an edit of a document in a ZIP between two parts can mean starting again.
- **Saving, stdio only.** With directories named in `PAPERLESS_MCP_DOWNLOAD_DIRS` (default: none) the two tools take `save_dir`, `save_as` and `confirm`. This supersedes the rule of decision-7 that downloads are never written to the host for this one case. Every other rule of decision-7 stays, and so does decision-27. HTTP never registers a variant with a path and never gives the setting to a service; `check_transport` refuses at call time as the second lock.
- **Saving is not read-only**: it changes the host. The saving variant carries `read_only_hint=false`, `destructive_hint=true`, `idempotent_hint=false`, `open_world_hint=false`, also for its attaching calls. Read-only mode does not offer it: the plain variants are registered instead, so downloading stays possible. `PAPERLESS_MCP_READ_ONLY` means: nothing changes in Paperless or on the host.
- **What is written.** One new plain file of mode 0600 in an existing directory inside an allowed one. An existing plain file is replaced only after a confirmation (decision-6, decision-15, decision-44): a dialog where the client can show one, else the refusal and `confirm=true`. The text says what would be saved (the kind, document and version; for a ZIP how many documents and up to ten ids) and names the file and the directory it really lies in (always resolved: a link inside an allowed directory, and an allowed directory that is a link itself; the refusal and the 254 characters apply to that path) as destinations of decision-44: quoted, in full, up to 254 characters each; what cannot be shown so is refused, also with `confirm=true`. Its `scope` binds what the text does not tell apart: the file that would be replaced (device, inode, size, mtime and ctime, so that a file written again and dated back counts as another one; measured on macOS on APFS, FAT32 and exFAT; on a file system whose ctime is no change time the others have to do), the directory (device, inode), and what would be saved (document, kind, version and the checksum and size of its file; for a ZIP the ids in the order given, the content, the compression and the naming). Nothing is open on the disk or at Paperless while a dialog waits. A ZIP always holds the newest versions and is built only after the acceptance, so its scope names no version: here the documents are only read, and what cannot be undone is the replaced file. Links, files with a second name, special files, directories and names starting with a dot or holding characters nobody sees (HFS+ ignores some, so U+200C before `.git` is `.git`) never; no directory is created; every unusable directory has one answer.
- **The ending comes from the type Paperless detected**, by a fixed table of the types Paperless 3.5.1 consumes (`.pdf` for the archive version, `.zip` for a ZIP, mail and Office included), never from the title or the upload. A proposed name with another ending gets the right one behind it; `save_as` with another ending is refused. A type outside the table is saved as `.bin`: refusing it would lose a file, and any other ending would come from text others write.
- **Limits** are constants: 2 GiB, 30 minutes, 256 MiB of the disk stay free, checked against the known size before the file is made and again before every piece, as a ZIP has no size beforehand and a size can be wrong. Paperless may take 600 s to build a ZIP to save, 120 s for a part. A saved file is compared with the size and SHA-256 of the metadata; a ZIP must be whole, from its first member to its end record.
- **MEDIA_TOOLS** (decision-11): both tools stay in it. Attaching, parts and saving share one form: a JSON text block and at most one embedded resource; a saving call has only the text block.
- **Windows**: saving is refused, as uploading by path is (no `dir_fd`, no `O_NOFOLLOW`).
- No tool joins the default profile: it stays at 20 of 20 (decision-34, decision-42).
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- With download directories a client may ask before every call of the two tools.
- Parts of large files are slow (the file is read again for each) and load Paperless; nothing is stored for them on the server.
- A ZIP holds the newest versions only: Paperless has no versions in `bulk_download`.
- POSIX has no rename or link that compares, so another process of the same user can interfere in the microseconds after the last look, in three ways: swap the target of a confirmed replacement (it is replaced, a link never followed); swap the hidden temporary name (what it put there gets the file's name); move the directory away (the file is then where the directory went).
- If the temporary name cannot be removed after the hard link was made, the call says so: until it is removed the file has two names and is not replaced.
- Nothing limits how many parts or saves run at once; each reads the file in pieces and lets other calls run, but costs Paperless and the network its whole size.
- A killed server leaves a hidden `.mcp-*.part`. The allowed directory itself is opened by its resolved name, as for uploads.
- A text document can be saved under a name some program reads, such as `requirements.txt`; the documentation says to name a directory of its own.
- With `confirm=true` the file seen at the start of that call is replaced; an acceptance in the dialog covers only the file that was shown. Opening the old file, a sync client, a backup or a `chmod` can change its ctime: the call then asks again, or ends with "changed meanwhile" if it happens during the download.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- A bounded copy of the ZIP in memory between calls (64 MiB, 10 minutes, one per user): parts would survive an edit on PostgreSQL, at the price of documents kept in memory between calls. Rejected.
- A copy on disk between calls: HTTP must not write documents to the host. Rejected.
- A tool of its own for saving (`save_document_file`): plain downloads would keep their read-only hint, but it needs a slot of the default profile or a new opt-in toolset and thus two switches. Rejected by the owner.
- Hashing a document to its end for every part: proves each part at the price of the whole file for a first kilobyte. The comparison at the end gives the same proof once.
- The ending of the name Paperless proposes: for a type Paperless has no ending for, that is the end of the title. Refusing unknown types: a file the user may read could not be saved.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/tools/test_downloads.py`: the matrix of variants (HTTP with the setting, stdio without, with, and with read-only mode), annotations, the second lock; `tests/tools/test_registry.py`: path allowlist and the rules of every tool on the saving server.
- `tests/services/test_file_sink.py` (marker decision-45): directories, names, endings, races, failures, the reserve, cancellation, a limit of the kernel and, in CI, a disk that is really full.
- `tests/services/test_file_sink.py` and `test_file_saving.py` (marker decision-44): what an acceptance covers. `tests/tools/test_downloads.py`: the dialog on both eras; `tests/tools/test_confirmation_text.py`: text and descriptions of the saving variants.
- `tests/services/test_file_parts.py`, `tests/services/test_file_saving.py`, `tests/tools/test_downloads_transport.py`; contract tests of `open_document_file` and `open_archive`.
