---
id: decision-64
title: >-
  Look-alike double quotes are escaped in names, service URLs are returned
  without credentials
date: '2026-11-24T11:05:24Z'
status: accepted
---
## Context

The reviews of decision-44 and decision-48 left gaps (PLMCP-107). The confirmation drops the credentials of a service URL, but `get_app_config` and the result of `update_app_config` returned them. A webhook URL with several @ before its host was accepted, although parsers disagree about which @ ends the credentials. Names of other people reach a message through `quoted()`, which puts them in double quotes when they hold an apostrophe: a look-alike double quote in such a name seems to close them.

## Decision

Supersedes, in decision-44, how text the server did not write stands in a message (quoted, cut to 60 or 80 characters), the list of webhook URLs that are refused, and that credentials of a service URL are dropped only from what a confirmation shows. Everything else in decision-44 stands.

- **Names.** Such text stands quoted and cut as before. Besides, what can be read as a double quote is escaped as in an excerpt (decision-48, the same characters): typographic, full-width and ornamental quotes, primes, and the second of two single quotes side by side. Always, also within single quotes, so a name looks the same wherever it stands. An escape counts with the characters it is shown with, so the cut stays at 60 (80), and it is never cut itself; where a message shows both ends of a text, each end holds whole escapes.
- **What stays in a name:** the plain double quote, which decides about the quotes around it; the backslash, so a literal `\u201d` in a name looks like the escape; single apostrophes and guillemets; everything else of other scripts (letters, marks, corner brackets). Hebrew gershayim (U+05F4) stay between two Hebrew letters and are escaped at the edge of a word. The Yiddish double yod (U+05F2) stays: two single ones look the same and are escaped nowhere.
- **Webhooks.** A webhook URL with more than one @ in its authority is refused where decision-44 refuses the other unclear forms: when the workflow is enabled and its triggers, actions or switch change, also with `confirm=true`. A @ in path, query or fragment is none of them. Switching off, renaming, repairing and deleting such a workflow work.
- **Results.** The configuration that `get_app_config` and `update_app_config` return holds the three `*_endpoint` settings without their credentials, and `shown_in_part` names those that have some. If it is not certain what the credentials are (decision-44), "a URL that is not shown" stands for the URL; written back, that is refused. What is stored does not change.

## Consequences

- A client that writes a URL back as it was returned removes its credentials; the confirmation says so, `confirm=true` on the first call does it. The descriptions of the tool and the field warn.
- Nobody can read the credentials of a service URL back through these tools, or tell whether two are the same.
- Names with German, English or Chinese quotation marks show escapes, and because these count, less of a long one.
- The list of look-alikes is made by hand (decision-48). A look-alike single quote within single quotes is not escaped, and neither are look-alikes in a new destination, which is shown in full and as it is. A stored one that is shown with both ends (an old mail host or folder) is escaped.
- Look-alike letters in front of the @ of an address stay unmarked, as in decision-44.

## Alternatives

- Escaping only within double quotes: nothing else can seem to be closed, but one name would look different in two messages, and the rule would depend on the choice of the quotes.
- Escaping everything an excerpt escapes: combining marks and letters of other scripts would make names unreadable.
- The note "with credentials (not shown)" in the value itself: it would be written back as part of the URL.

## Enforcement

- `tests/domain/test_errors.py`: each look-alike, the cut, both ends, names in other scripts.
- `tests/services/test_confirmation_destinations.py`: webhook URLs with several @, and that such a workflow can be made safer.
- `tests/services/test_admin_system.py` and `tests/tools/test_admin.py`: no credentials in either result; what happens to a URL written back.
- `tests/tools/test_confirmation_text.py`: hostile names with look-alike quotes in the refusal of every tool.
