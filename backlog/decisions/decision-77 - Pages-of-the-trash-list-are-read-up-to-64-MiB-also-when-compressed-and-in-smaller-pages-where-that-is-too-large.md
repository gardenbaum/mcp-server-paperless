---
updated_date: '2026-11-24T21:33:47Z'
id: decision-77
title: >-
  Pages of the trash list are read up to 64 MiB also when compressed and in
  smaller pages where that is too large
date: '2026-11-24T21:15:38Z'
status: proposed
---
## Context
<!-- SECTION:CONTEXT:BEGIN -->

The rows of the trash carry the whole text of their documents. Decisions 47, 54 and 55 bound every read of them to 64 MiB but one: the pages of `list_trash`, which restoring and emptying by ids, `get_trashed_file` and the filter `ids` fall back to, and the one request of `empty_trash(all=true)`. Those had no limit, and their answers were unpacked whatever they unpack to. The bounded reads refuse a compressed answer, so the same bound there would stop restoring by ids behind a proxy that compresses (PLMCP-111).

httpx2 unpacks gzip, deflate and zstd in pieces of at most 1 MiB since 2.16.0, which is the minimum this server installs. So the limit can be enforced on what an answer unpacks to, while it is read.

<!-- SECTION:CONTEXT:END -->
## Decision
<!-- SECTION:DECISION:BEGIN -->

- `TrashReader.list_trash` has the limit of 64 MiB of the other reads, on what its answer unpacks to. It still asks for a compressed answer and takes gzip, deflate and zstd; another encoding is refused unread, with the proxy as the cause.
- Where ids are looked for in pages of 100 entries, a page that is too large is read as pages of 10 and those as single entries, then in pages of 100 again. An entry over the limit on its own is passed. If an id is then missing, the answer is an error that says so, not that the id is not in the trash.
- A page of `list_trash` that is too large is an error that names a smaller `page_size`, for a single entry the next page.
- `empty_trash(all=true)` reads one request as before. If that is too large it reads the pages, and goes on only if every page counts the same and they hold exactly that many different entries; otherwise it refuses and names `document_ids`.
- The other reads of rows stay as decision-54 has them: uncompressed or refused.
- Supersedes one statement of decision-55: "Ids still work through pages of 100 entries read with the ordinary request of `list_trash`, which had no size limit before either". Supplements decisions 47 and 54.

<!-- SECTION:DECISION:END -->
## Consequences
<!-- SECTION:CONSEQUENCES:BEGIN -->

- Restoring and emptying by ids work behind a compressing proxy and with large texts; only an entry of more than 64 MiB cannot be found, and neither can an id be ruled out beside one.
- A page that is too large costs up to 64 MiB before it is refused; a block of 100 entries at most 111 requests.
- Emptying the whole trash with more than 64 MiB of text takes several requests, and on PostgreSQL, where pages of equal dates can overlap, may be refused where it was possible before. It never empties entries it did not see.
- If the trash shrinks while its pages are read, the search ends instead of failing with a page that does not exist; an id that is not found is explained as before.

<!-- SECTION:CONSEQUENCES:END -->
## Alternatives
<!-- SECTION:ALTERNATIVES:BEGIN -->

- A plain bound that refuses compressed answers: restoring by ids would not work behind such a proxy.
- Unpacking compressed answers in the bounded reads, too, so that filters and the duplicate check of uploads work behind such a proxy: that changes decisions 47 and 54, so it is left to the owner.

<!-- SECTION:ALTERNATIVES:END -->
## Enforcement

- `tests/adapter/test_trash.py`: the limit to the byte for every encoding, the memory a small answer that unpacks to much may take, other and broken encodings, and ids found behind a compressing proxy.
- `tests/services/test_trash_filters.py`: smaller pages, an entry that is too large, and emptying everything in pages or not at all.
- `tests/contract/test_trash.py`: the limit on the fake and on Paperless 3.5.1. `tests/e2e/test_trash.py`: restoring by ids from Paperless 3.5.1 through a transport that has every answer compressed.
