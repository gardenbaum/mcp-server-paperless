---
id: decision-70
title: >-
  read_documents returns OCR text only when asked, at most 20,000 characters a
  call
date: '2026-11-24T13:44:29Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
AC 3 of PLMCP-80 wants many documents read in one call, with selectable fields, within a size limit, and text only on request. Decision-4 keeps OCR text out of list rows. `GET /api/documents/` with `fields=content` would send the whole text of every row (a text can be a million characters).

The owner chose on 2026-11-24 among 20,000 characters, 60,000 and no text (design decision 10), approved the toolset `bulk` (16), and that notes are refused to users who may not view notes (15): "Entwurf 80: Nummern 1 bis 20 wie von dir empfohlen, samt den dort genannten Limits." (draft 80: numbers 1 to 20 as you recommend, with the limits named there) (PLMCP-80).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `read_documents` (toolset `bulk`, off by default, read-only) shows at most 100 documents a call, with the fields that are named; only these are asked for. `id` and `web_url` always. A field without a value is left out of its row.
- `content` only when it is named. It is read document by document through the single read, one after the other, the first `content_chars` characters each (2,000 unless asked otherwise, at most 20,000), until the call has **20,000 characters** of text or 40 s have passed since it began (decision-50's number). `content_total` and `content_next` say how to go on with `get_document_content`. An error of one text (a 500, a 403, one timeout) is told in `notices`, the document is named in `failed` (could not be read in this call; not in `left`, which must not start with it for ever) and the others go on. What concerns every request ends the texts of the call: HTTP 429, after the client's own retries (every read is tried up to three times, as everywhere), a rejected credential, no connection, or two outages in a row (502, 503, 504, a timeout; one alone can be of its document), which are then both in `left`; the rest is in `left`.
- The answer has at most **100,000 characters** (its JSON; notices at most 5,000 of them, this server's own before those on the query). Then whole rows are left out: `truncated`, and `left` names them, as it names rows without the text that was asked for. A row that would not fit alone loses the largest of notes, custom fields, tags and text, and `too_large` names what is missing.
- `notes` needs the permission to view notes, checked with the notes of the first document: Paperless sends the notes in the list also to a user without it, and the call is refused then, also if no document could be asked.
- Paperless's answer is bounded by 64 MiB and may not be compressed.
- Supplements decision-4: the list rows of every other tool stay without text.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A page with text is one request per document, until 20,000 characters are reached.
- FastMCP sends the answer twice (text and structured), so 100,000 characters are up to 200,000 on the wire.
- In read-only mode `bulk` offers exactly this tool.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/services/test_read_documents.py` (marker decision-70): both limits, 40 s with an injected clock, 429, a lasting error of one text, a row too large alone, notes; as a property: every document is shown or named.
- `tests/tools/test_read_documents.py`: the limits through the server, read-only mode, the restricted user. `tests/contract/test_selection.py`: every field, and the notes of a user who may not view notes, on both backends. `tests/adapter/test_selection.py`: the retries before a 429 is told.
