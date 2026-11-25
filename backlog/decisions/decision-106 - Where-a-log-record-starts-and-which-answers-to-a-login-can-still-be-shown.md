---
id: decision-106
title: Where a log record starts, and which answers to a login can still be shown
date: '2026-11-25T16:01:48Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-80 hides what a mail server answered to a login up to the next record of the log, and names as not covered an answer that looks like the start of one. Its test for a start was a bracket and a date. Read in the source of Paperless 3.5.1 and of imaplib: a lone carriage return in an answer reaches the log as it is (a NO to AUTHENTICATE, or a BYE at any command), and `/api/logs/` reads it as the end of a line. Every real line of the three logs starts with `[date time,ms] [LEVEL] [logger] `.

Time, level, logger and the known texts can all be written by the server, so nothing in a line tells a forged start from a real one. Hiding more (up to a known text, up to the end, everything after an error) hides most of `mail.log` and `paperless.log` of an account whose login fails for long, and still shows what a server says elsewhere.

The owner decided on 2026-11-26, "Alles wie von dir empfohlen." (everything as you recommend), that the start is tightened and proven, and that AC 4 of PLMCP-81.1 says: "the answer to a failed login is in no result, error or log line, except where a hostile server forges the start of a log record or repeats it outside the login".
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-80 and decision-66; what they hide stays hidden.

- A record of a log starts only with the whole form: date, time with milliseconds, a level in capitals and a logger, each in brackets with one blank between, and a blank or the end of the line behind. Any other line belongs to the record before it, or at the top of a log to one whose start is missing.
- Two holes are known and accepted: a server that forges the whole start of a record with a lone carriage return, and a window that begins behind the real start (the 1,000 lines `get_log` reads, a small `limit` of `paperless_api_get`), so that the first forged start ends what is hidden.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- What a hostile server shows this way it can show without it: it knows the password, can accept the login and repeat it in a folder name, a subject, a sender or a file name, hence in a document title.
- Not affected: errors of processed mail and texts of tasks are one text and hidden to their end; `explain_mail_import` shows known texts only.
- A line of a log in another form, if a plugin wrote one, counts as the rest of a record: hidden at the top of a log, and hidden with a login error or the credentials of a URL before it.
- If Paperless changes the form of its log, more is hidden, not less.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives
<!-- SECTION:ALTERNATIVES:BEGIN -->

- Hiding until the next record of a known text: the server forges that text first.
- Hiding everything behind a login error, or behind the last one in a file that is read whole: nearly all of both logs while a login fails, and workflow explanations with them.
- Asking Paperless to escape line breaks in its log: the remedy; not in our hands.

<!-- SECTION:ALTERNATIVES:END -->
## Enforcement

- `tests/domain/test_login_answers.py`: what is a start and what is not; the two holes.
- `tests/e2e/test_mail_import.py` with `tests/e2e/echo_server.py`: a forged part of a start shows nothing, a whole one is shown in the logs and not in the explanation.
