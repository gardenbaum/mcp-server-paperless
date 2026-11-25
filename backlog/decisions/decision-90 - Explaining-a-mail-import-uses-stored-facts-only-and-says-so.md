---
updated_date: '2026-11-25T09:52:30Z'
id: decision-90
title: Explaining a mail import uses stored facts only and says so
date: '2026-11-25T09:52:28Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
AC 3 of PLMCP-81: why a mail was not imported, without a connection of this server (decision-7). The API has no dry run of rules. A mail server searches in its own way, Paperless takes rules in their order, and `mail_fetch` tasks say nothing of their outcome (`result_data` is null, proven in PLMCP-81.2): a fetch that was skipped is only in the log `paperless`.

The owner approved decision 7a of the design of PLMCP-81 on 2026-11-24 ("Alle wie empfohlen", all as recommended, in the question "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?"), and on 2026-11-25 that a skipped fetch is taken from `paperless.log`, staff only, else "not known" ("Überall wie von dir empfohlen bitte.", everywhere as you recommend, please).
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Supplements decision-7, decision-18 and decision-80.

- `explain_mail_import` is read-only, contacts only Paperless's API, and every answer begins with the sentence that it is no dry run and ends with the closest way to see what a rule does.
- Each enabled rule is compared with what the caller knows of the mail: `ok`, `no` (most likely keeps it out; the mail server decides) or `unknown`. A day at the edge of the age, the body, keywords and the effect of earlier rules are `unknown`. `ok` promises nothing.
- Records are found by the subject, as Paperless cuts it. Tasks are shown if the user may view them.
- Logs (staff users): lines of `mail` and `paperless` that name the account or one of its rules, and skipped fetches; with a known subject no line about another mail. They go through the redaction of `get_log`. Without them the answer says that a skipped fetch is not known.
- One request at a time, nothing is started 40 seconds after the call began; rows, lines and the answer (50,000 characters) are bounded. What was not read or is left out stands in `not_checked`, and `complete` is false. A rate limit or a rejected credential ends the call.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The explanation can be wrong where a server searches differently; it never says that a mail would be imported.
- Rules of other users that the caller does not see are not checked, and that is said.
- A log line that names the account of another user with the same name is shown: staff see all logs anyway.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives
<!-- SECTION:ALTERNATIVES:BEGIN -->

- A dry run by a connection of this server: decision-7 forbids it, and it would need the password.
- Only naming `get_log`: the admin toolset may be off, and AC 3 asks for logs.

<!-- SECTION:ALTERNATIVES:END -->
## Enforcement

- `tests/services/test_mail_explanation.py`: every check in its results, records, tasks, logs, gaps, bounds, no secret.
- `tests/tools/test_mail.py`, `tests/e2e/test_mail_import.py`: the tool, with a mail server and a user who is no staff.
