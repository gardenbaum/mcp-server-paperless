---
id: decision-54
title: The trash stays opt-in; looking into it shows only what its list shows
date: '2026-11-24T08:02:50Z'
status: accepted
---
## Context

The toolset `trash` is off by default; it listed, restored and emptied, and showed a title, an id and a date. PLMCP-78 makes entries readable and asks whether reading and restoring belong in the default profile, where 19 of 20 places are used (decision-34, decision-42). The owner approved the design on 2026-11-23: the trash stays opt-in.

## Decision

- No trash tool joins the default profile. Restoring can undo a deliberate deletion and is rare, and since PLMCP-82 the assistant is told of toolsets that are off (decision-41). Every tool that can move something to the trash says how to restore it and that this needs the trash toolset.
- Two read-only tools join the toolset: `get_trash_entry` (every field the API tells, the text in slices) and `get_trashed_file` (thumbnail, original or archive). `get_trashed_file` joins `MEDIA_TOOLS` (decision-11), because its payload is an image or a file. Files over the download limit are read in parts as decision-45 says; Paperless tells no checksum of a file in the trash, so each part hashes the whole stream.
- Paperless serves the file of a trashed document to anyone with a view grant, but lists the entry only to its owner, or to all if it has none, and to superusers. Both tools require that the entry is in the list of the user, so they widen nothing.
- Of a trashed document Paperless shows the text, thumbnail and file of its first version, and no notes, custom field values, later versions, history or file metadata. The tools say so and name `restore_from_trash` as the way; newer versions are "restore first" (approved).
- `permissions` is read by a second bounded request with `full_perms`, which sends it in place of `user_can_change` and `is_shared_by_requester` (those stay from the first). The owner decided on 2026-11-24 to read it ("Bitte alles wie von dir empfohlen umsetzen"). Of a deleted version they are the document's, read as `set_permissions` reads them, and so is `user_can_change`: access follows the document, the grants of the version row are always empty. `is_shared_by_requester` is null then: Paperless tells it of no live document this server reads. If a read fails, the entry is shown without it and `limits` names the gap.
- A file or thumbnail that Paperless does not send (404) is called missing only for a deleted version whose metadata, readable at its document, says so (PLMCP-105). Of a document in the trash there is no metadata, so the error names both causes and claims none.
- Every read of whole rows is bounded to 64 MiB, asks for an uncompressed answer and refuses a compressed one unread, with the proxy as the likely cause: behind such a proxy `get_trash_entry` and filters do not work.

## Consequences

- 93 tools. The list of toolsets that are off names two more tools (37 bytes): `OVERVIEW_GUIDE_LIMIT` rises from 2800 to 2835, approved by the owner on 2026-11-24 ("wie von dir empfohlen umsetzen").
- Text and fields of one entry cost two reads (the second for `permissions`) of up to 1,000 rows with their texts; only the wanted row is kept. `get_trashed_file` reads the rows once for every call, so for every part up to 64 MiB, to check that the entry is in the user's list: no other endpoint tells that (`/root/` and the files answer view grants, too). This is because the list has no field selection and no other endpoint answers a trashed document.
- `get_document_thumbnail` shows the thumbnail of a trashed document to anyone who may view it, as before; whether it should is left to a task of its own.

## Alternatives

- `list_trash` and `restore_from_trash` in `core`, against the last place and one removal: rejected by the owner.
- A flag on `search_documents`: the API cannot search the trash.

## Enforcement

- `tests/tools/test_registry.py`: the budget of the default profile, the media tools, and the table of tools that trash with its self-test. `tests/tools/test_trash.py`: off by default, and what read-only mode leaves.
- `tests/contract/test_trash.py`: fields, text, files and view grants on the fake and on Paperless 3.5.1. `tests/adapter/test_trash.py`: every documented field is read. `tests/services/test_trashed_documents.py`: slices, parts, the missing archive, and what a view grant does not open.
