---
id: decision-78
title: A thumbnail of what is in the trash is refused and that is said
date: '2026-11-24T21:21:17Z'
status: proposed
---
## Context

Paperless 3.5.1 serves the thumbnail (and the preview and the file) of a document or version in the trash to everyone who may view it: the actions resolve with `include_deleted=True`. Every other read of it answers 404. So `get_document_thumbnail` and the resource `paperless://documents/{id}/thumbnail` showed deleted documents without a word. decision-54 left open whether they should (PLMCP-102). Downloads, parts, page images, contact sheets and PDF tools read the document or its metadata first, which is 404 in the trash, and the raw toolset refuses file endpoints, so only the thumbnail is affected.

## Decision

Proposed; the owner decides. What is built:

- The tool and the resource answer an error that the id is in the trash, and show no image. That is safer than the image with a note: nothing deleted is shown by a tool for live documents, as with every other one, and decision-54 ties the thumbnail of an entry to the trash list of the user (`get_trashed_file`), which a view grant does not open.
- It is said to be in the trash only where Paperless served the thumbnail, so to users who may view it. Everybody else gets the error of before.
- Whether a document is live is asked after the image came: one light list request for a document. Only an id that is not listed takes more: a version is shown if its document is listed and its own metadata is there, so a version left alive under a trashed document is not.

## Consequences

- One more request per thumbnail, four for the id of a live version.
- If one of these requests fails, the thumbnail is not shown.
- A refusal takes, after the thumbnail: two requests for a trashed document, four for a deleted version, three for a version left alive under a trashed document.
- A document emptied from the trash right after its thumbnail was read gets the error of one that does not exist, because `/root/` no longer knows it. Only what is emptied after that request is still called "in the trash".

## Alternatives

- Show the image with a note that it is in the trash: a user with a view grant would go on seeing what its owner deleted, and the resource has no report to carry the note.
- Answer plainly "does not exist": does not say how to get it back.
- Only the metadata: for Office files Paperless may ask Tika for it, for every thumbnail.

## Enforcement

- `tests/contract/test_trash.py`: what Paperless 3.5.1 and the fake answer for a trashed document and version, with and without a view grant.
- `tests/services/test_files.py`, `tests/tools/test_files.py`, `tests/e2e/test_trash.py`: the refusal of the tool and the resource, also for a restricted user, and that live versions are still shown.
