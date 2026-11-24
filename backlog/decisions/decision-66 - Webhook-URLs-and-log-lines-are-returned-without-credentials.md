---
id: decision-66
title: Webhook URLs and log lines are returned without credentials
date: '2026-11-24T12:16:36Z'
status: accepted
---
## Context

decision-64 left the workflow tools open (PLMCP-112): `get_workflow`, `list_workflows`, the results of `create_workflow` and `update_workflow` and `paperless_api_get` returned webhook URLs with user name and password. Paperless also writes the URL of each webhook it sends into its log (`get_log`, `/api/logs/`), and repeats a URL it refuses in its error. Unlike the settings of decision-64, actions are replaced as a whole list, and every update gives them new ids.

## Decision

Supplements decision-64 (its forms apply to webhook URLs), decision-25 (what the raw toolset takes out) and decision-44 (what a confirmation says of a webhook).

- **Results.** A webhook URL stands in every result of the workflow tools without its credentials. `shown_in_part` of its action then holds `webhook.url`. If it is not certain what the credentials are, "a URL that is not shown" stands for the URL.
- **Written back.** The shown form on an action that carries the id of the stored one keeps the stored URL, also with `confirm=true` and in another order; the note of the result says "Kept as stored, with any credentials". Only the id tells which action is meant, never the place or the URL. The shown form is compared without whitespace around it and in any case of scheme and host. Without an id, or with that of another action, the shown form of any stored URL of the workflow that has, or may have, credentials is refused, and so is one id on two actions that would both hold its stored URL, in whatever form it is given. A webhook with an id that is not (or no longer) in the workflow is refused, whatever its URL, with a hint of its own. "a URL that is not shown" is never written.
- **Removed.** A URL with an empty user name (`https://@host/path` or `https://:@host/path`) is written without any credentials, with or without an id, also where its path or query holds an @. Several empty ones (`https://@@host`, `https://:@:@host`) and whitespace before are the same; an empty user name is never written before a host and a stored one alone is not called credentials, but `https://@user@host` is none: it is written while the workflow is disabled and refused otherwise (decision-64).
- **Another URL** never gets stored credentials. Where an action with the id of a stored one loses them by that or by the empty user name, the confirmation says "remove the credentials of the stored URL" and the note says "Removed the credentials of"; where it gets other ones, "replace", and where that cannot be told, "remove or replace" and "any credentials". If no webhook carries the id of a stored hidden URL, while one carries no id or that of an action without a webhook, confirmation and note say that the stored credentials of N webhook URL(s) are removed, unless such a webhook gives the whole URL.
- **Copies.** A new URL that is the shown form of one that another workflow stores with credentials is written without them, with `create_workflow` and `update_workflow`; confirmation and note say so and do not name that workflow (owner, 2026-11-24). Credentials are never copied. The list of workflows the tool reads anyway is used: a user who may not view workflows, or a read that fails, is refused as before and nothing is written.
- **Confirmations** say "with credentials (not shown)" of a webhook whose new or kept URL has some, and "with, or possibly with, credentials" where that cannot be told, by the test of the results. Those of service settings use it as well: a stored URL that results do not show is "a URL that is not shown" in them, a new one is refused without its text.
- **Raw.** In `paperless_api_get` a `webhook` that is text, and every `url` at or below a `webhook`, has the form of the results, and so has every `*_url` and, in `/api/status/`, every `url`: the vendored docs do not say that `redis_url` comes without credentials. Texts in `result_data`, `error` and `*_error` are treated as text (below). `redacted` counts it and `note` says so; a value that is cut loses its credentials first.
- **Text.** In log lines (`get_log`, `/api/logs/`), in the error texts of Paperless (keys included) and in the errors of tasks, the system status, processed mail and share bundles, the URLs lose their credentials by the same function as a value. Where they are certain, nothing else goes. Where they are not, "a URL that is not shown" (once per line) stands for all from the first URL to the last word with an @: credentials may hold spaces, quotes and other URLs, so removing more is preferred to showing a password. A URL is whatever a client may take for one: also without or with backward slashes, and without a scheme a word with a : or // (or `\\`) before its @, or a port or [ after it. Lines of a log are joined to the record they belong to (a record starts with its date) and keep their number. `get_log` reads all the lines it may and shows the last ones asked for. The time is linear. `note` (of `get_log` and of the raw answer) or the end of a refusal says so, once.
- **An @** is also the full-width and the small one (U+FF20, U+FE6B) and, in the authority, the encoded one that no real @ follows: such a URL is not shown. This applies to the service URLs of decision-64 as well.

## Consequences

- Owner, 2026-11-24: a URL with an @ after its host (`https://host/@name`) is "a URL that is not shown", as a service URL is: `https://us/er:pw@host` looks the same and holds a password. It can be kept, replaced and written, but not read. The stricter rule for full-width and encoded @ holds for service URLs too, and what stands between a URL and a later e-mail address in a log record or an error may go.
- A workflow copied from what is shown has no credentials. The server looks at the URLs of other workflows only to say so.
- A second webhook with the same address and no credentials needs the empty user name, because it could also be a copy that lost its id.
- A word `x:y@z` (`From:bob@x.org`, `{"k":"a@b"}`) is taken for credentials even without a URL: `user:password@host` looks the same. `mailto:` is no user name. So does a word that holds a URL with an @ after such a start (`u:p/https://q@h`, `{"id":3,"url":"https://u:p@h"}`): the whole word is not shown.
- `x@h`, `x@h/path`, `x@h?a` and `x@h.` stay: they look like e-mail addresses, mail folders and file paths. A URL whose : / and @ are all encoded or escaped stays as well.
- A file path with a : or // (`C:\x\a@b.pdf`, `\\server\a@b`) before an @ is taken for credentials.
- Stay shown: an encoded @ after an unescaped / or ? (`https://u:SE/CRET%40h`), which is in many ordinary URLs, and one encoded twice (`%2540`).
- The head of a log that is cut (undated lines before its first record, or a log without dates) loses every line up to the last one with an @, also if that is an e-mail address: the URL it ends can stand before the cut. It always applies, also where the log starts there: whether Paperless caps `limit` is not known. `get_log` reads the 1000 lines it may read, so a cut is rare.
- A `*_url` is treated so in every raw answer, not only in `/api/status/`; `redis_url` is the only one the vendored docs know.
- A record or error that holds a URL without credentials and after it an e-mail address or a URL with credentials loses what is between them.
- A secret in the path or query of a URL is returned and shown (decision-64).

## Alternatives

- Matching by place or by URL where the id is missing: two actions may have the same shown URL, and a list may be reordered; credentials would reach the wrong account.
- Writing the URL without credentials where the id is missing: a client that writes everything back would lose them unnoticed.
- Showing a URL whose @ stands after its host, as `_webhook` reads it: it would show the password of a URL whose credentials hold an unescaped / ? or #.
- Leaving logs and errors as they are: the same credentials, one tool further.

## Enforcement

- `tests/services/test_workflow_credentials.py`, `tests/tools/test_workflows.py`: results, write-back by id, what is refused, removal, confirmations.
- `tests/services/test_raw.py`, `tests/services/test_admin_system.py`, `tests/adapter/test_errors.py`, `tests/domain/test_urls.py`: raw answers, log lines, errors.
- `tests/contract/test_workflows.py`: Paperless stores the URL as sent and gives new ids. `tests/e2e/test_raw.py`: raw answers of Paperless.
