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
- **Written back.** The shown form on an action that carries the id of the stored one keeps the stored URL, also with `confirm=true` and in another order; the note of the result says "Kept as stored, with any credentials". Only the id tells which action is meant, never the place or the URL. Without an id, with an id that is not (or no longer) in the workflow, or with that of another action, the shown form of any stored URL of the workflow that has, or may have, credentials is refused, and so is an id that would keep its credentials on two actions. "a URL that is not shown" is never written.
- **Removed.** A URL with an empty user name (`https://@host/path` or `https://:@host/path`) is written without any credentials, with or without an id, also where its path or query holds an @. An empty user name is never written before a host.
- **Another URL** never gets stored credentials. Where an action with the id of a stored one loses them by that or by the empty user name, the confirmation says "remove the credentials of the stored URL" and the note says "Removed the credentials of".
- **Confirmations** say "with credentials (not shown)" of a webhook whose new or kept URL has some.
- **Raw.** In `paperless_api_get` the `url` of every `webhook` object has the form of the results, wherever it is nested. `redacted` counts it and `note` says so; a value that is cut loses its credentials first.
- **Text.** In log lines (`get_log`, `/api/logs/`) and in the error texts of Paperless, what stands between a :// and the last @ of the same word is left out and "(credentials not shown)" put in front. Credentials may hold spaces: in an error, and in a log line that speaks of a webhook, everything up to the last @ after the :// goes.

## Consequences

- A URL with an @ after its host (`https://host/@name`) is "a URL that is not shown", as a service URL is: `https://us/er:pw@host` looks the same and holds a password. It can be kept, replaced and written, but not read.
- A workflow copied with `create_workflow` from what is shown has no credentials: the server does not look for them in other workflows.
- A second webhook with the same address and no credentials needs the empty user name, because it could also be a copy that lost its id.
- An error or a webhook's log line can lose text between a URL and an @ after it, such as an e-mail address.
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
