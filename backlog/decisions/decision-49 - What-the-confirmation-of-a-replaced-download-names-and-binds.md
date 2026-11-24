---
id: decision-49
title: What the confirmation of a replaced download names and binds
date: '2026-11-24T00:41:10Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-45 says what the confirmation of a replaced download names and what its `scope` binds. The review after its merge found that the text did not say what would be saved, that a link inside an allowed directory hid where the file really lies, that a file written again in the same size and dated back passed as the same, and that bytes another process wrote into a fresh reservation counted as ours. The directory was shown as it was given, so the length limit was about that text.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supersedes, in decision-45, the sentences of "What is written" on what the text of the confirmation names and what its `scope` binds, and the consequence on what `confirm=true` and an acceptance cover. Everything else in decision-45 stands.

- **The text** says which file would be replaced (name, size, day of its change), in which directory, and with what: the kind, document and version, or a ZIP with the number of its documents and up to ten ids.
- **The directory** is the one the file really lies in. It is always resolved: a link inside an allowed directory, and an allowed directory that is a link itself. File name and directory stay destinations of decision-44, quoted and in full; the refusal of what cannot be shown and the limit of 254 characters each apply to the resolved path, also with `confirm=true`.
- **The scope** binds the file that would be replaced (device, inode, size, mtime and ctime), the directory (device, inode), and what would be saved: the document, kind and version with the checksum and size of its file; for a ZIP the ids in the order given, the content, the compression and the naming. A ZIP always holds the newest versions and is built only after the acceptance, so its scope names no version: the documents are only read, and what cannot be undone is the replaced file.
- **The identity** that the scope binds is compared again right before the replacement; if it differs, the call ends with "changed meanwhile" and nothing is replaced.
- **Without hard links** the name is taken with an empty file. If that file is not empty when it is first looked at, somebody wrote into it: the call ends with "changed meanwhile" and those bytes stay.
- With `confirm=true` the file seen at the start of that call is replaced; an acceptance in the dialog covers only the file that was shown.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Opening the old file (macOS notes the time in an attribute), a sync client, a backup or a `chmod` can change its ctime: the call then asks again, or ends with "changed meanwhile" if it happens during the download.
- The ctime was measured on macOS on APFS, FAT32 and exFAT. On a file system whose ctime is no change time the other values have to do.
- A new file in a directory with a longer path is still saved; only a replacement there is refused.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Comparing the content of the old file instead of its ctime: reads a file of any size twice for a case nobody does by accident.
- Showing the directory as it was given next to the resolved one: two paths in a dialog, of which only one is true.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/services/test_file_sink.py` and `tests/services/test_file_saving.py` (marker decision-49): what the text names, what an acceptance covers, and the reservation another process wrote into.
- `tests/tools/test_downloads.py` and `tests/tools/test_confirmation_text.py`: the dialog of the saving tools and its text.
