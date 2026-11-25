---
id: decision-99
title: >-
  Left out of the rest of the Paperless API: what another way does, what the
  server must not touch, and what does nothing
date: '2026-11-25T14:45:04Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
PLMCP-83 closes what the coverage table still lists as open: each operation and field gets a tool or an exclusion the owner approved, quoted with its date.

On 2026-11-24 the owner approved the design of PLMCP-83: asked in a question dialog "Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?" (do you approve the drafts 81, 87, 88, 89, 90, 83 and 100 with all recommended options, with the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010?), the owner chose "Alle wie empfohlen" (all as recommended). So every line its tables mark as an exclusion, and the omissions in its decisions 2, 5, 6, 7, 9, 10 and 11, are owner-approved exclusions of 2026-11-24.

Read in the source of paperless-ngx 3.5.1; what is marked (E2E) is also held by a test on 3.5.1.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
Left out, owner-approved as quoted above (2026-11-24):

**The server's own account and its ways in** (decision-25 denies them to the raw reader too):

- `GET` and `PATCH /api/profile/`: the answers hold the API token. Name, e-mail and password go through `update_user`, for users who may change users.
- `GET`, `POST`, `DELETE /api/profile/totp/`: the second factor of the server's own account; the GET makes a new secret.
- `POST /api/profile/generate_auth_token/` deletes the token this server runs on and answers the new one. `POST /api/token/` is a login with a password. `POST /api/profile/disconnect_social_account/` and `GET /api/profile/social_account_providers/` belong to a browser login.

**Another way does it:**

- `PUT` of correspondents, custom fields, document types, documents, groups, storage paths, tags and users: PATCH does it without resetting what is left out.
- `GET` and `PUT /api/config/{id}/`: there is one row, and the list and PATCH do it. `DELETE /api/config/{id}/` drops every setting and API key at once; `update_app_config` resets one by one.
- `GET /api/documents/{id}/preview/`: the bytes of the download, inline. `GET /api/documents/{id}/share_links/`: `list_share_links(document_id)` shows the same, the expired ones too.
- `POST /api/documents/{id}/email/`: deprecated; a JSON body is a server error in 3.5.1 (E2E). `email_documents` is used.
- `GET /api/search/`: three rows per kind, documents with their text. `search_documents`, `list_taxonomy` and the lists are used.
- Filters of the lists of correspondents, document types, tags, storage paths, custom fields, groups and users: the whole list is read and searched here.
- Of `GET /api/documents/`: the `__name__` filters, `id` and `tags__id` (names are resolved here and ids sent); `iexact`, `istartswith`, `iendswith` and `checksum__icontains` (contains, exact or the full text find them); times of day, `day`, `month` and `year` (the filters of whole days give the same documents, up to the time of day); `search` and `title_content` (text search does it); `all` (no longer in API version 10). `shared_by__id=true/false`: the schema is wrong, the user id is sent.
- `full_perms` of one correspondent, document type, storage path, tag or document: the grants are read from the lists.
- `owner` and `set_permissions` when a correspondent, document type, storage path or tag is created: create, then `set_permissions`.
- `created_date` (deprecated) and `remove_inbox_tags` of a document: `created` and `tags_remove`. `documents/{id}/download` `follow_formatting`: it changes only the proposed file name.
- Dates and `ordering` of the lists of share links and bundles: the rows show the dates, newest first.

**It does nothing, or harm:**

- `query:id` of the notes: the schema says "used only for DELETE".
- `document_ids`, `expiration_days` and `file_version` of a bundle's rebuild: Paperless does not read the body (E2E). `document_ids` is sent, because the schema asks for it.
- `data_type` of a custom field: the values of each type lie in columns of their own and nothing is converted, so every value vanishes from view (E2E).
- `from_webui` of nine requests says that the request came from the web UI and changes the recorded source and which workflows run: untrue here.
- `date_joined` of a user is set by Paperless. `app_logo` is a picture for branding and stays with the web UI.

**The other UI settings** (`POST /api/ui_settings/` beyond the saved views of decision-92) steer only the browser of the token's user and are not checked by Paperless: left out.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A user who may not change users cannot change the own name, e-mail or password here; the web UI does it.
- The reader of grants that replaces `full_perms` comes with PLMCP-83.4; until then `set_permissions` shows them when a change starts.
- The exclusions are rows of `docs/api-coverage.toml` and name this decision. What parts b to d of PLMCP-83 build stays open there.
- The README says of the coverage what the table allows (`coverage_statement`).
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_api_coverage.py`: a row needs the quote and the date from this file; the README's statement is tested against the table.
- `tests/e2e/test_left_out_operations.py` (marker decision-99): the evidence marked (E2E). These operations are no ports, so no fake holds them.
