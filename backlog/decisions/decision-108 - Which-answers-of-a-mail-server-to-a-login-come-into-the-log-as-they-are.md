---
id: decision-108
title: Which answers of a mail server to a login come into the log as they are
date: '2026-11-25T17:38:40Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-106 says that a lone carriage return reaches the log in "a NO to AUTHENTICATE, or a BYE at any command". The E2E run 45654754686 (job 139348966511, Paperless 3.5.1) shows that this is too much: after a BYE to AUTHENTICATE (PLAIN and XOAUTH2) the log says "socket error: EOF" and nothing of the answer. imaplib goes on reading after a BYE until a tagged answer comes; if the server closes the connection, the text of the BYE is lost. The E2E tests had accepted that, because the text of another case stood in the same log.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supersedes in decision-106 only the words "a NO to AUTHENTICATE, or a BYE at any command", and in decision-80 only "in an untagged BYE to a plain LOGIN as well as to AUTHENTICATE". The rest of both stands, the rules and the two holes included: a forged start of a record is possible with each kind of login.

An answer to a login comes into the log with its carriage return:

- to LOGIN in an untagged BYE;
- to AUTHENTICATE (PLAIN, XOAUTH2) in a tagged NO, and in an untagged BYE if a tagged answer follows.

It does not in a NO to LOGIN (escaped), nor in a BYE to AUTHENTICATE that ends the connection (lost).
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- No change of the server: what is hidden does not depend on the kind of the answer.
- Each case of the echo server must find its own credentials in the log, so that none passes for another's text.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives
<!-- SECTION:ALTERNATIVES:BEGIN -->

- Dropping the cases for AUTHENTICATE: they can forge a record as well.

<!-- SECTION:ALTERNATIVES:END -->
## Enforcement

- `tests/e2e/test_mail_import.py` with `tests/e2e/echo_server.py`: NO, BYE and a failed logout, and forged starts, for LOGIN, PLAIN and XOAUTH2.
