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

- **Names.** Such text stands quoted and cut as before. Besides, what can be read as a double quote is escaped as in an excerpt (decision-48, the same characters): typographic, full-width and ornamental quotes, primes, and the second of two single quotes with nothing visible or only marks between them. So are the double accents U+030B and U+030F where no letter or digit carries them. Always, also within single quotes, so a name looks the same wherever it stands. The escapes are made after the cut: it takes the same characters of a name as before, escapes do not count and are never cut. But one text takes at most 240 characters, its quotes and escapes included (one shown with both ends at 254: 600); what is longer is cut earlier, with the same mark, so that a hostile name does not push what matters out of a dialog.
- **What stays in a name:** the plain double quote, which decides about the quotes around it; the backslash, so a literal `\u201d` in a name looks like the escape; single apostrophes and guillemets; everything else of other scripts (letters, marks, corner brackets). Hebrew gershayim (U+05F4) stay between two Hebrew letters, niqqud in front of them included, and are escaped elsewhere. The Yiddish double yod (U+05F2) stays: two single ones look the same and are escaped nowhere.
- **Webhooks.** A webhook URL with more than one @ in its authority is refused where decision-44 refuses the other unclear forms: when the workflow is enabled and its triggers, actions or switch change, also with `confirm=true`. A @ in path, query or fragment is none of them. Switching off, renaming, repairing and deleting such a workflow work.
- **Results.** The configuration that `get_app_config` and `update_app_config` return holds the three `*_endpoint` settings without their credentials, and `shown_in_part` names those that have, or may have, some. If it is not certain what the credentials are (decision-44), "a URL that is not shown" stands for the URL.
- **Written back.** A value that equals what is returned for the stored URL means unchanged, also with `confirm=true`: what is stored stays, and the result names the setting as kept with its credentials. So the confirmation of decision-44 that a change only removes credentials is gone: they are removed by resetting the setting (null) first, or go with a different URL. "a URL that is not shown" is refused where it does not stand for the stored URL.

## Consequences

- Nobody can read the credentials of a service URL back through these tools, or tell whether two are the same. Credentials are the user name and password of a URL: a key in its query string is returned and shown.
- Removing credentials from a URL that stays takes two calls; between them the setting follows the server's environment.
- Names with German, English or Chinese quotation marks show escapes. So do words that use a listed sign as a letter: U+02EE and U+02BA always, and U+02BE, U+02BF, U+144A and U+16CC as the second of two side by side. Between letters they look like a closing quote with text right after it, so the place gives no safe exception.
- A name with a few such quotes is as long as before. One made of escapes shows 39 of its characters (19 of those beyond U+FFFF). The server caps no refusal; the longest of the tested worst cases has 3403 characters.
- Error messages and hints use the same form: a name that a model copies from one holds the escape, not the sign, and matches nothing. Names are to be taken from results.
- The list of look-alikes is made by hand (decision-48). Open: a look-alike single quote within single quotes; look-alikes in a new destination, which is shown in full and as it is (a stored one that is shown with both ends is escaped); look-alike letters in front of the @ of an address (decision-44).
- The URLs of webhooks are still returned with their credentials by the workflow tools: PLMCP-112.

## Alternatives

- Escaping only within double quotes: nothing else can seem to be closed, but one name would look different in two messages, and the rule would depend on the choice of the quotes.
- Escaping everything an excerpt escapes: combining marks and letters of other scripts would make names unreadable.
- The note "with credentials (not shown)" in the value itself: it would be written back as part of the URL.
- Counting escapes towards the cut: every name with such quotes would show less of its text. Not bounding them: one name could take 722 characters of a dialog.
- Taking a URL written back for a wish to remove the credentials: a client that writes all settings back would lose them.

## Enforcement

- `tests/domain/test_errors.py`: each look-alike, what is invisible between two, the cut, both ends, the longest form, names in other scripts.
- `tests/services/test_confirmation_destinations.py`: webhook URLs with several @, and that such a workflow can be made safer.
- `tests/services/test_admin_system.py` and `tests/tools/test_admin.py`: no credentials in either result; a URL written back keeps what is stored.
- `tests/tools/test_confirmation_text.py`: hostile names with look-alike quotes, and names made of escapes only, in the refusal of every tool.
