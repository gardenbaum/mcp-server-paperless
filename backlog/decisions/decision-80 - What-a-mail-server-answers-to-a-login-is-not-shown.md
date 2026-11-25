---
updated_date: '2026-11-25T08:20:36Z'
id: decision-80
title: What a mail server answers to a login is not shown
date: '2026-11-24T22:55:20Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The `error` of a processed mail is a whole Python traceback, and `mail.log` has the same texts. For a login that failed they hold what the mail server answered. A broken or hostile server may repeat what it was sent: the password or access token, also in base64. decision-66 knows credentials in URLs only. The owner approved decision 5a of the design of PLMCP-81 on 2026-11-24 ("Alle wie empfohlen", all as recommended, in the question "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?"): hide the answer in the mail tools, in `get_log` and in the raw tool. An automated review of the first version found that an answer can forge the end of its error and that a cut log can lack the part that tells of a login; the main agent asked to fail closed (2026-11-24). The owner approved on 2026-11-25 that more is hidden than decision 5a names (the rest after a mail error, the undated head of a log, and what follows "Error while retrieving mailbox NAME:"): "Überall wie von dir empfohlen bitte." (everywhere as you recommend, please).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-66; its functions (`text_without_credentials`, `lines_without_credentials`) do it, so errors, task texts, log lines and raw answers are all covered, before any cut.

- In a text with a frame of `mailbox_login`, everything from the first error of `imaplib` or `imap_tools` on is "(answer of the mail server not shown)", once per line, to the end of the text. Nothing tells for sure where an answer ends, so no later part of the chain is believed.
- In a record of the log, so is everything after "Error while authenticating account NAME:" and "Error while retrieving mailbox NAME:", also where the answer starts with a line break.
- The lines of a log before its first dated record are "(rest of a log record not shown)", all of them: the start that tells of a login or a URL is missing.
- The answer goes before the credentials of URLs, so that a URL in an answer takes nothing else with it and cannot hide where the answer starts.
- `get_processed_mail` gives the whole error otherwise, 20,000 characters a call, continued with `error_from`.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Lost with the answer: the chained error behind it, which only says that the login of the account failed, its frames in Paperless, and an error of the logout that follows. Kept: the frames before, the kind of the error, and the line of the log that names the account.
- Lost at the top of a log: the rest of one record. `get_log` reads the 1,000 lines it may, so this is rare, but a record longer than that, or a log without dates (Paperless writes none), shows nothing. `paperless_api_get` with a small `limit` can show nothing of a traceback. decision-66 lost only the lines up to the last one with an @.
- An account name with ": " in it loses its end in the line of the log.
- "Error while retrieving mailbox NAME:" also names what is no answer: a refused or reset connection, an unknown host, a TLS failure, a search that failed. The line then only says that it failed; `test_mail_account` tells whether connecting or signing in fails. It is hidden because an answer to the logout after a failed login stands there as well.
- An error in a traceback that does not quote the server (a refused or reset connection, TLS) stays.
- Not covered: what a server says to a later command or to a fetch, and a line of the log that the server forges to look like the start of a record. That needs a carriage return in its answer, in an untagged BYE to a plain LOGIN as well as to AUTHENTICATE; nothing tells such a line from a real one.
- The rules know the texts of imaplib, imap_tools and Paperless 3.5.1; the live tests of PLMCP-81.2 show when they change.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives
<!-- SECTION:ALTERNATIVES:BEGIN -->

- Hiding only up to the next chained error: an answer of several lines forges it.
- Showing the rest when it ends with the error of Paperless: the same.
- Hiding it in the mail tools only: the same words are in `get_log` and `/api/logs/`.
- Showing it: AC 1 of PLMCP-81 to the letter, and the password with it.

<!-- SECTION:ALTERNATIVES:END -->
## Enforcement

- `tests/domain/test_login_answers.py`: tracebacks, forged ends, log records, cut heads, planted secrets, linear time.
- `tests/adapter/test_mail.py`, `tests/services/test_admin_system.py`, `tests/services/test_raw.py`, `tests/services/test_processed_mail.py`: processed mail, `get_log`, raw answers, the parts of an error.
