---
updated_date: '2026-11-24T07:11:32Z'
id: decision-52
title: A trash over 64 MiB is not checked before an upload, and that is said
date: '2026-11-24T07:10:57Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-47 fails the duplicate check, and so refuses every upload, when the trash with the text of its documents is larger than 64 MiB. The user can only empty or restore parts of the trash to upload again. Above 500 entries the same check goes on and names the gap. Asked whether this case should be a gap or a failure, with the recommendation to upload and say so, the owner answered on 2026-11-24 "bitte wie von dir empfohlen" (please as you recommend) (PLMCP-104).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- This supersedes one part of decision-47: "A trash larger than that is a failed check with its true cause", with the second half of "An archive too slow for 25 seconds, or a trash over 64 MiB, cannot be uploaded to". Everything else of it, the limit itself included, stands.
- **What it is.** Exactly the case that the answer with the first 500 entries is larger than 64 MiB. Outages, timeouts, the 25 seconds of the whole check, compressed answers, missing checksums and a trash that does not add up still refuse, also with `confirm=true`.
- **Before sending** nothing of the trash is compared then, neither its documents nor its deleted versions; nothing is read in smaller parts, because pages of the trash overlap on PostgreSQL. `duplicates.not_checked` (for `upload_documents` the `not_checked` of the call) says so in one sentence. Without a match nothing is asked and the file is sent.
- **The confirmation** for a match elsewhere carries the same sentence, so an acceptance given before the trash grew too large, or the other way round, does not count.
- **The count at the end** stays: a trash that was too large is counted with a request for one entry, again at the end, and a change is read again once and then refuses, as before. If even one entry is larger than the limit there is no count, and the sentence covers that too. If one entry is too large at the end only, the trash has changed.
- **upload_documents** (decision-50) takes the gap over with its one reading and counts for every file. If the trash outgrows the limit during a call, the files that follow have the gap; the sentence stands once for the call.
- **After a rejection** (`rejected`, `get_task`) nothing changes: Paperless names the document, only the title of one in the trash is left out, and that is no gap of a comparison.
- Supplements decision-44 and decision-50.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- With a trash that large, a file that is only in the trash is uploaded without a question; a document that moves between the trash and the rest during the check is noticed by the count only. What Paperless reports after consumption for a document includes the trash.
- Up to 64 MiB are still transferred before the limit is hit, twice if the trash changes. An instance that needs more than the 25 seconds for it still refuses.
- One more request in this case.
- With several entries of one deletion date, one of them larger than 64 MiB on its own, PostgreSQL may show another one at each count; that reads as a change and can refuse.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Refuse, as decision-47 did: rejected by the owner.
- Read fewer entries: which ones is not defined on PostgreSQL where dates are equal, and how many fit is not known before reading.
<!-- SECTION:ALTERNATIVES:END -->


## Enforcement

- `tests/services/test_duplicates.py` and `tests/services/test_batch_duplicates.py`: the gap, the limit to the byte, the confirmation, the count, and that every other failure refuses. `tests/services/test_batch_uploads.py`: named once for a call.
- `tests/adapter/test_duplicates.py`: the requests, and other failures on the wire. `tests/contract/test_duplicates.py`: the gap on the fake and on Paperless 3.5.1.
- `tests/tools/test_upload_large_trash.py`: the three tools, the dialog, and that every other failure refuses.
