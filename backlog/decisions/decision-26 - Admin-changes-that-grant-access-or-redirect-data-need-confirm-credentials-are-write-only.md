---
id: decision-26
title: >-
  Admin changes that grant access or redirect data need confirm; credentials are
  write-only
date: '2026-11-17T00:31:00Z'
status: accepted
---
## Context

The admin toolset (PLMCP-31) manages users, groups, the application configuration, background tasks and bulk download. Probed on Paperless 3.4.2:

- A user is a superuser, a staff user or a plain user; superusers see and change everything, staff users read the logs. Group membership and permission codenames decide the rest. Deleting a user leaves what they owned without an owner (every user with the model permission then sees it) and removes their grants; deleting a group removes it from its members and from object grants.
- A user's password and the three API keys of the configuration (`llm_api_key`, `llm_embedding_api_key`, `remote_ocr_api_key`) come back masked, and the mask is as long as the secret. A password or key made only of asterisks is ignored on a write. Anyone signed in may read the configuration; a PATCH needs `change_applicationconfiguration`. The configuration decides which AI and OCR service receives document text.
- `POST /api/tasks/run/` is for superusers and starts only `train_classifier`, `sanity_check` and `llm_index`. Training replaces the model automatic matching uses; the index sends every document to the embedding service.
- Decisions 6, 12, 15, 17, 18 and 19 already require `confirm` for what cannot be taken back, publishes data, or gives access.

An LLM with these tools could make itself or someone else a superuser, lock the server out of Paperless, leak a password or key through a result, an error or a log line, or point document text at another host.

## Decision

- Users and groups: the toolset reuses `list_users` and `list_groups` of the permissions toolset (decision-19) for listing and does not repeat them; its tools take a username or group name, or an id, and resolve names like `set_permissions` does (decision-5).
- `delete_user`, `delete_group` and every `update_app_config` need `confirm`. `create_user` needs it for a superuser or staff user. `update_user` needs it when it changes superuser or staff status, deactivates or reactivates the user, sets a password, or changes groups or permissions; names, e-mail and a rename need none. `update_group` needs it when the permissions change, a rename needs none; `create_group` needs none (a group has no members yet). A call that would change nothing is neither sent nor confirmed.
- `run_task` needs `confirm` for `train_classifier` and `llm_index`, and none for `sanity_check`, which only reads. `acknowledge_tasks` and the reads need none.
- The refusal uses `ConfirmationRequired.for_effect` (decision-15) and names who or what is affected: each setting with its old and new value (secrets are never shown), the groups added or removed, the permissions granted or taken away, and that a change of an AI or OCR endpoint changes where document text goes.
- The account the server signs in with cannot be deleted, deactivated or stripped of its superuser status through these tools, with or without `confirm`: the server could not undo it.
- Credentials are write-only as in decision-18: a password and the API keys enter as `SecretStr` (tool parameters with `writeOnly` and format `password`, and a validator that never raises, so a rejected input is never quoted), are unwrapped in one place (the adapter's request body), and no domain model, tool output, error, confirmation text or log line holds a password, a key or its mask. The adapter's row models turn the keys into "holds a value" and do not declare the password. Blank and asterisk-only secrets are refused without echoing them.
- A refusal by Paperless (403) becomes `PermissionDenied` with a hint naming the permission that is needed (and that superusers have all of them).
- Bulk download is a read; the archive is attached as an embedded resource only within the download limit, otherwise the report says why and what to do instead (decision-11, decision-7).
- This supplements decisions 6, 12, 15, 18 and 19 and changes none of their rules.

## Consequences

- Every change of access, every deletion and every change of the configuration takes two calls by design; a client that never passes `confirm` can read everything and create plain users and groups, but cannot elevate, delete or reconfigure.
- Whoever loses a password or key sets a new one; nothing can read it back, not even through this server.
- The tool descriptions and refusal texts are part of the contract; the surface snapshot shows them.
- Accepted gap: group membership and permission codenames are checked by Paperless only (an unknown codename is a 400); the confirmation lists them as given.

## Enforcement

- Registry tests (tests/tools/test_registry.py): `create_user`, `run_task` and the destructive admin tools carry an optional `confirm: bool = False`; no output schema has a credential-like field; the password and API key inputs are `writeOnly`.
- Adapter tests (tests/adapter/test_admin.py): no password or key in rows, results, errors or logs; asterisk secrets are refused before anything is sent; every 403 carries its hint.
- Service tests (tests/services/test_admin_accounts.py, test_admin_system.py): every refusal text, nothing written without `confirm` (the fake is created without `allow_writes`), the server account is protected, no secret in any message.
- Tool tests (tests/tools/test_admin.py), contract tests on the fake and live 3.4.2 (tests/contract/test_admin.py) and E2E (tests/e2e/test_admin.py) run the flows through the real adapter.
