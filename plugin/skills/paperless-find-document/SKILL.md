---
name: paperless-find-document
description: Find one document in paperless-ngx from a loose description such as sender, topic, rough date or amount, and link to it. Use when the user looks for a letter, invoice, contract, policy or other document in their Paperless archive.
---

# Find a document

Take the description from what the user said. This needs the tools of the paperless server of this
plugin, with the default toolset core. If a tool named below is missing (server not configured or
not running, read-only mode, or disabled by the user), say which, do the steps that need no
missing tool, and do not work around it with other tools, scripts or direct API calls.

Find this document in Paperless: what the user described

1. Work out filters from the description: correspondent, document type, tags, a date range and
   words to search for. Call `list_taxonomy` to learn the exact names; do not guess them.
2. Run `search_documents`, first with the strongest clues. If nothing is found, widen the search
   (fewer filters, other words, a wider date range) and try again; if there are too many hits, add
   a filter.
3. Compare the best candidates with `get_document`. Read the text with `get_document_content` only
   to tell close candidates apart or to find a detail (an amount, a date, a number).
4. Answer with the best match first: title, correspondent, date, and the web_url link. List other
   candidates briefly, and say how sure you are. If nothing fits, say what you tried and ask for
   more detail.

This prompt only reads. If the user then wants a document changed (title, tags or anything else),
that is a change. Never change anything on your own. Show the user the changes you propose (what
changes, on which documents or items, and why), then wait for the user to confirm before you call
any tool that writes. Apply only what the user confirmed, and report what was changed.
