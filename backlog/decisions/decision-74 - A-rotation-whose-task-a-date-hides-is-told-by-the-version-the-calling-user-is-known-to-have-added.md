---
id: decision-74
title: >-
  A rotation whose task a date hides is told by the version the calling user is
  known to have added
date: '2026-11-24T18:32:49Z'
status: accepted
---
## Context

Paperless 3.5.1 cannot record the `consume_file` task of a file whose metadata holds a date among the custom fields. A rotation takes the metadata of the document along, so the task of a rotated document with such a field is never listed, and `rotate_documents` could only answer unknown. Decision-67 left rotating out ("Rotating is not covered"). On 2026-11-24 the owner approved to check it by the newest version before and after, in a task of its own after PLMCP-79.4 ("Alles wie von dir empfohlen", everything as you recommend) (PLMCP-79.5). How a version is told was decided within that task: a version made by somebody else must never be taken for the rotation, and what cannot be told apart stays unknown.

A newer version alone proves nothing: anybody who may change the document can add one, also a rotated one, at any time.

## Decision

- Where no task is listed for a document of a rotation, a date among its custom fields explains it, and the call waits, its new version is looked for (`PdfWriter.find_rotations`). It stands in the result like that of a task, without a task id. Documents with a listed task are followed as before, in the same result.
- A version is taken only if all of this holds:
  - it is newer than the one the document showed when it was read before the request, and was added since the queued task of the request started;
  - its file is named `{document id}_rotated.pdf`, as Paperless names the file of a rotation and of nothing else it makes;
  - the history of the document says in its entry "Version Added" for this version that **the calling user** added it. 3.5.1 writes that entry in the same transaction as the version, with the user of the request that led to it: the caller for an upload (`update_version`), and for an operation the user who asked for it. A caller cannot choose it. This is what excludes everybody else, also an administrator's rotation and an upload under that name;
  - it is the only such version of the document.
- What the calling user did elsewhere (another client, the web UI) is excluded as in decision-67: no other rotation of this user ran at the same time or has started since; no listed task for the file name was unfinished when the request started; and the rotations of this user that ended in the last hour before must be done, that is, as many versions with such a name, added by this user since the first of them started, must be found on the documents changed since as these rotations had documents. That last condition is looked into once every version is there, or at the end, and waited for within the bound.
- A doubt leaves every hidden document of the request unknown, and the message says why: the history may not be read (`view_logentry`, or the audit log is off), the user is not known (`view_uisettings`), the queued task has no times, a list is too long to reach back an hour or to all changed documents, or one of the conditions above.
- Without a wait nothing is looked for: the answer is unknown, and the tool says so.
- Supplements decision-67, and replaces its one consequence that rotating is not covered.

## Consequences

- A user without `view_logentry`, and an instance with `PAPERLESS_AUDIT_LOG_ENABLED=false`, get the answer unknown as before: without the history Paperless does not say who added a version.
- A rotation of this user in the last hour that failed or left a document out, or whose version was deleted, leaves the later ones of dated documents unknown for the rest of that hour. So do more than 100 documents changed, or 100 consumption tasks or operations listed, in that time, and two rotations at the same time.
- An operation of more than an hour before whose file is consumed only now would not be noticed. It would be a rotation of the same document by the same user, never one by somebody else.
- Two users who share one Paperless account are one user here.
- Times are compared as Paperless wrote them, as in decision-67.
- Per search: one read per document, two for the user and the operations, one for the tasks, and for each version that could be the rotation one of its file name and one of the history; after an earlier rotation also one list and the same for the versions added since.
- A listed task is still told by its file name, time and owner alone. For a document with a date 3.5.1 lists none, so there it could only be an upload of the same user under that name in the 3 seconds of the lookup.
- The description of `rotate_documents` grows by one sentence; no limit changes.

## Alternatives

- The newest version before and after alone, or with time and file name: somebody else's rotation, or an upload under that name, would be taken.
- Comparing the pages or the bytes: a rotation of the same file by the same angle by somebody else gives the same pages.
- Without the history, trusting that a staff user sees every operation and task: it would serve staff whose audit log is off, with a second set of conditions for one answer.
- Remembering the own requests: rejected by the owner on 2026-11-24.
- Making the rotation here and uploading it as a version, whose task is listed: another operation than Paperless's own, not asked for.

## Enforcement

- `tests/adapter/test_pdf_rotations.py`: every condition and every doubt.
- `tests/contract/test_pdf.py` (marker decision-74): on the fake and on Paperless 3.5.1, with a rotation and an upload of somebody else at the same time.
- `tests/services/test_pdf_rotations.py`, `tests/tools/test_pdf.py`, `tests/e2e/test_pdf.py`: what the tool answers.
