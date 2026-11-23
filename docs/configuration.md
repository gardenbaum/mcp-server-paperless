# Configuration

Everything is set through environment variables. Two are required: where Paperless is and the
token to use. The rest decides what the model may do and how much it may move.

- [Paperless](#paperless)
- [Server](#server)
- [What the model can do](#what-the-model-can-do): toolsets, read-only mode, disabled tools
- [Toolsets](#toolsets), one section each
- [Uploads and downloads](#uploads-and-downloads)
- [Startup and logs](#startup-and-logs)

The [Claude Code plugin](../plugin/README.md) asks for the Paperless settings and the server settings
that narrow or widen what the model can do, and leaves the rest at their defaults.

The HTTP transport has its own variables in [Run over HTTP](http.md), the OIDC layer in
[Connect claude.ai](claude-ai-connector.md).

## Paperless

| Variable | Meaning |
|---|---|
| `PAPERLESS_URL` | Base URL of the Paperless instance (required) |
| `PAPERLESS_TOKEN` | Paperless API token (required). Create it in Paperless under your profile menu, "My Profile", API auth token. The server acts as this user and sees and changes only what this user may |
| `PAPERLESS_PUBLIC_URL` | Base URL for links shown to users (`web_url`, share links), if it differs from `PAPERLESS_URL`, for example when the server reaches Paperless under a container name; an empty value counts as unset |

Requires paperless-ngx 3.x (API version 10). 3.4.2 is the oldest supported release and the one
every change is tested against; the latest 3.x release is tested weekly. paperless-ngx 2.x is not
supported.

## Server

| Variable | Meaning |
|---|---|
| `PAPERLESS_MCP_TOOLSETS` | Toolsets to offer, comma-separated, or `all` (default `core,ai`). See [Toolsets](#toolsets) |
| `PAPERLESS_MCP_READ_ONLY` | `true` to offer only tools that change nothing (default `false`). See [What the model can do](#what-the-model-can-do) |
| `PAPERLESS_MCP_DISABLED_TOOLS` | Tools never offered, whatever the toolsets say, e.g. `ask_paperless_ai,get_ai_suggestions` |
| `PAPERLESS_MCP_UPLOAD_DIRS` | Directories the upload tools may read files from by path (default empty: uploading by path is off). See [Uploads and downloads](#uploads-and-downloads) |
| `PAPERLESS_MCP_MAX_UPLOAD_BYTES` | Largest file the upload tools take, in bytes (default 10485760 = 10 MiB, at most 104857600) |
| `PAPERLESS_MCP_MAX_DOWNLOAD_BYTES` | Largest file `download_document` attaches, in bytes (default 524288, at most 33554432) |
| `PAPERLESS_MCP_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` or `CRITICAL` |
| `PAPERLESS_MCP_STARTUP_TIMEOUT` | Seconds the startup check may take (default 5) |

A misspelt toolset or tool name stops the server at startup with a message that names the known
ones; so does a selection that leaves no tool.

A variable that is set but empty stops the server too, so that an empty `PAPERLESS_MCP_READ_ONLY`
never means writable; leave it unset for the default. Only an empty `PAPERLESS_MCP_DISABLED_TOOLS` or
`PAPERLESS_MCP_UPLOAD_DIRS` is an empty list.

## What the model can do

Three settings narrow the tools a client is offered. A tool that is not offered cannot be called.

- **Toolsets** (`PAPERLESS_MCP_TOOLSETS`). The catalogue is split into [toolsets](#toolsets).
  Only `core` and `ai` are on by default, so a client starts with a small set of tools and the
  model's context is not filled with tools you never use. Add what you need, for example
  `PAPERLESS_MCP_TOOLSETS=core,ai,pdf,trash`, or `all`.
  The assistant is told which toolsets are off, what each is for and how to switch them on, in
  the server instructions and in the result of `get_instance_overview`, which also lists their
  tools (see [Switching a toolset on](#switching-a-toolset-on)).
- **Read-only mode** (`PAPERLESS_MCP_READ_ONLY=true`). Offers only the tools that change nothing
  in Paperless, from whichever toolsets are selected: searching, reading, listing, downloading.
  The prompts that need a writing tool are not offered either.
- **Disabled tools** (`PAPERLESS_MCP_DISABLED_TOOLS`). Takes single tools away by name.

Whatever is offered, the Paperless permissions of the token's user still apply, and tools that
can lose data or widen access have a confirmation step: called without `confirm=true` they
change nothing and answer with what they would do, so the client can show it to you first. Every
tool also tells the client whether it only reads or may change or remove data (its MCP
annotations), which clients use for their own approval prompts. The [tool
reference](tools.md) lists the kind of every tool.

### Switching a toolset on

Only you can: the assistant cannot change the selection, neither for the server nor for a session
(decision-41), and is told to ask you. It is told which toolsets are off and what each adds, so
that it can say what to set instead of calling a feature missing. The instructions give the
setting first, as `PAPERLESS_MCP_TOOLSETS=core,ai,<name>` (what is on now plus the toolset needed,
never `all`, which offers every tool), then the list of toolsets that are off. Nothing is listed
that read-only mode, disabled tools or a Paperless without the capability (Paperless AI for `ai`)
leaves out, and a toolset that read-only mode or disabled tools cut down is said to offer "some of
its tools" (the overview names them). When Paperless did not say whether AI is on (the user may
not view the UI settings, or it did not answer at startup), `ai` is marked "needs Paperless AI,
not checked", whether it is on or off. The overview is built on each call from what Paperless
says then: a toolset that Paperless now says it lacks is not offered, and one it cannot say
about is marked "not checked", on or off, by the current answer; what is on stays as the start
found it. A toolset that is selected but was hidden at the start (Paperless AI was off) and that Paperless
now offers is listed in the overview under `offered_after_restart`; it is in neither list of
toolsets to switch on, and a restart is all it needs. The instructions never say so: they are
written at the start, when nothing has changed yet. The selection is read at startup, so the server must restart:

| How you run it | Where to set `PAPERLESS_MCP_TOOLSETS` | What to restart |
|---|---|---|
| Claude Code plugin | The option Toolsets: `/plugin configure mcp-server-paperless@gardenbaum` | Claude Code |
| Cursor plugin | Not offered (only the URL and the token are): add the server by hand instead | Cursor |
| stdio (`claude mcp add`, Claude Desktop) | `env` of the server entry in the client's configuration; `-e` of `docker run -i` for the [stdio image](container.md) | The client (or reconnect the server in it) |
| HTTP | The environment of the process | The process |
| Container image | `environment` of the service, or `-e` | Recreate the container (`docker compose up -d`, or `docker run` again): a plain restart keeps the old environment |

Give the full list (`core,ai,trash`), not just the new toolset; `all` offers every tool. The option of the plugin must not be empty.

## Toolsets

The [tool reference](tools.md) lists every tool; this section says what each toolset is for and
what to know before switching it on.

### `core`

On by default. Find and read documents (`search_documents` with full text, similarity and
structured filters, `get_document`, `get_document_content` in slices, thumbnails and downloads),
file them (`update_document`, notes), upload new ones, follow background tasks, and maintain
tags, correspondents, document types, storage paths and custom fields. `run_saved_view` runs a
saved view that `list_taxonomy` lists, with the rows and paging of `search_documents`. Names are
accepted wherever an id is: an unknown or ambiguous name is an error that lists the candidates.
`delete_document` moves a document to the Paperless trash; `delete_note` and
`delete_taxonomy_item` are final. All three need `confirm`, and so do the changes of
`update_document` and `update_taxonomy_item` that cannot be taken back (handing a document to
another owner, removing options of a select field).

### `ai`

On by default, and offered only while Paperless AI is enabled: `ask_paperless_ai` and
`get_ai_suggestions`. These tools are read-only, but each call makes Paperless query its LLM
backend, which may cost money and sends document text to it: a client that auto-approves
read-only tools will run them unasked. Use `PAPERLESS_MCP_TOOLSETS=core` to leave them out.

### `bulk`

Off by default. `bulk_edit_documents` changes many documents in one step (tags, correspondent,
type, storage path, custom fields, trash, reprocess) and `bulk_edit_taxonomy` deletes many tags,
correspondents, document types or storage paths or sets who owns and may see them. Both work in
three steps: `dry_run=true` previews how many objects change, a call without `confirm` is
refused with a description of the effect, `confirm=true` executes. Paperless checks every object
first: if one may not be changed, none is. Deleting taxonomy is final (it has no trash).

### `versions`

Off by default: `list_document_versions`, `upload_document_version`,
`set_document_version_label`, `delete_document_version`, `merge_documents_as_versions`. Works
with the file versions of documents. `delete_document_version` and
`merge_documents_as_versions` need `confirm`, and a merge removes the merged documents for good.

### `views`

Off by default: `list_saved_views`, `get_saved_view`, `create_saved_view`, `update_saved_view`
and `delete_saved_view`, to read and maintain saved views. Running a view is `run_saved_view` in
`core`.

### `trash`

Off by default: `list_trash`, `restore_from_trash`, `empty_trash`. Lists what was deleted,
restores it (a deleted version is re-attached to its document) and empties the trash.
`empty_trash` never defaults to everything: it needs `document_ids` or `all=true`, and `confirm`
in both cases, because it removes documents for good.

### `pdf`

Off by default: `rotate_documents`, `merge_documents`, `split_document`, `delete_pages`,
`edit_pdf`, `remove_document_password`, `reprocess_documents`. Runs Paperless's server-side PDF
operations; each returns the background tasks Paperless starts (`wait_seconds` waits for them)
and page ranges are checked against the page count first. Deleting originals (they move to the
Paperless trash), dropping pages from what a document shows and reprocessing need `confirm`.
`reprocess_documents` does the same as the `reprocess` operation of `bulk_edit_documents` but
returns its tasks.

### `sharing`

Off by default. Publishes documents to people outside Paperless: `list_share_links`,
`create_share_link`, `delete_share_link`, `list_share_bundles`, `create_share_bundle`,
`rebuild_share_bundle`, `delete_share_bundle` and `email_documents`. A share link or bundle is a
public URL on `PAPERLESS_PUBLIC_URL` (else `PAPERLESS_URL`) that anyone who has it can open
without logging in until it expires (`expires_in_days`) or is deleted. Publishing, the delete
tools and `email_documents` need `confirm`, and `email_documents` names the recipients and
documents in its refusal. E-mail needs a mail server set up in Paperless
(`PAPERLESS_EMAIL_HOST`); without one the send fails and says so.

### `permissions`

Off by default: `set_permissions`, `list_users`, `list_groups`. Shares documents, tags,
correspondents, document types and storage paths and hands them over: `set_permissions` sets the
owner and the users and groups who may view or change, by name or id. Its default mode `merge`
only adds to the grants and mode `replace` makes each list you give exactly that list. Granting
access cannot be taken back, so every call that changes anything needs `confirm` (also a merge
and any change of the owner), and the refusal names who gains and who loses access.
`bulk_edit_taxonomy` (bulk toolset) also sets permissions of taxonomy objects, behind `confirm`
and with a `dry_run` preview (it empties the lists you leave out, `set_permissions` keeps them),
and `update_document` can change a document's owner along with other edits. `list_users` and
`list_groups` add to `list_taxonomy` the superuser flag, group memberships and paging. Listing
users or groups needs the `view_user` or `view_group` permission in Paperless.

### `mail`

Off by default. Manages the mail import: `list_mail_accounts`, `get_mail_account`,
`create_mail_account`, `update_mail_account`, `delete_mail_account`, `test_mail_account`,
`process_mail_account`, `list_mail_rules`, `get_mail_rule`, `create_mail_rule`,
`update_mail_rule`, `delete_mail_rule` and `list_processed_mail` (compact rows).

- Passwords are write-only: they are accepted when an account is created or changed and are
  never returned, quoted or logged, nor is any OAuth token (Gmail and Outlook accounts are set up
  in the Paperless web UI).
- Rules take tags, correspondent, document type and owner by name or id.
- `confirm` is needed to delete an account or a rule, to fetch an account now with
  `process_mail_account` (its rules can delete or move mail on the mail server, which cannot be
  undone), for a rule that is enabled and deletes or moves mail (when it is created, or when a
  change widens what it reaches), to point an account at another server, port or security
  without a new password (Paperless would send the saved password there) or to turn encryption
  off, and to hand a rule to another owner.
- `test_mail_account` logs in with the saved password and changes nothing; a failed login is a
  result, not an error.
- `process_mail_account` only queues the fetch: follow it with `list_processed_mail` and the
  Paperless task list.

### `workflows`

Off by default: `list_workflows`, `get_workflow`, `create_workflow`, `update_workflow`,
`delete_workflow`. Manages the workflows Paperless runs by itself, with triggers and actions as
nested objects and tags, correspondents, document types, storage paths, custom fields, users and
groups given by name. An enabled workflow keeps acting on every matching document, so
`create_workflow` and `update_workflow` need `confirm` when it moves documents to the trash,
e-mails them, calls a webhook, removes tags or other values, or changes owner or permissions
(disabled workflows and plain filing, such as tags and titles, need none), and `delete_workflow`
always does. Password removal and applying AI suggestions are not written by these tools (they
are shown when read), webhook header values and passwords are never shown, and a setting that
does not belong to its trigger or action type is an error.

### `admin`

Off by default. Administers the instance: users (`get_user`, `create_user`, `update_user`,
`delete_user`), groups (`get_group`, `create_group`, `update_group`, `delete_group`), the
application configuration (`get_app_config`, `update_app_config`), logs (`list_logs`,
`get_log`), background tasks (`list_tasks`, `acknowledge_tasks`, `run_task`) and
`bulk_download`, which attaches the files of several documents as one ZIP archive within the
download limit. `list_users` and `list_groups` of the `permissions` toolset find the users and
groups.

- Anything that gives or takes away access needs `confirm`, and the refusal names who is
  affected: creating a superuser or staff user or a user with a password, groups or permissions;
  changing a user's e-mail address, status, password, groups or permissions; deleting a user or a
  group; changing what a group grants.
- Every `update_app_config` needs `confirm` and lists each setting with its old and new value,
  because it applies to every user and the AI settings decide which service gets document text.
- `run_task` needs `confirm` to train the classifier or rebuild the AI index; a sanity check
  only reads and needs none.
- Passwords and API keys are write-only: never returned, quoted or logged.
- The account the server signs in with cannot be deleted, deactivated or stripped of its
  superuser status through these tools.
- Paperless decides who may do what: most tools need the matching Paperless permission, the
  logs are for staff users and `run_task` for superusers.

### `raw`

Off by default (`all` selects it). An escape hatch: `paperless_api_get(path, params)` reads any
path under `/api/` of the configured Paperless with GET (for example
`/api/documents/12/history/`), as the current user. It is read-only and cannot reach another
host:

- The path must be `/api/` followed by plain names (no absolute URLs, `//host`, backslashes,
  `@`, query or percent-encoding, `..` or empty segments; filters go in `params`), redirects are
  not followed, and a path without its trailing slash is answered as a redirect error.
- Endpoints with secrets (`profile`, `config`, `mail_accounts`, `token`, `oauth`), files
  (`download`, `preview`, `thumb`, `bulk_download`) and the language-model call `ai_suggestions`
  (use `get_ai_suggestions`) are refused, and so is the param `format`.
- `next`/`previous` links become page numbers, requests are never retried, only JSON is
  returned, values of keys such as `password`, `*token*`, `*_api_key` and the webhook `headers`,
  `params` and `body` are replaced with `[redacted]`, and an answer over 20 000 characters is cut
  with `truncated` true (narrow it with `page_size`, `fields` or filters).

The rules are decision-25 (`backlog/decisions/`).

## Uploads and downloads

**Uploads.** `upload_document` and `upload_document_version` take a file as base64 content. The
size limit is `PAPERLESS_MCP_MAX_UPLOAD_BYTES`, over stdio and over HTTP. Base64 content is a third
larger than the file and must be written out by the caller in one tool call, so real uploads are
far smaller. The size is checked on the text before anything is decoded, but the request body, its
text and the decoded bytes are in memory together, over HTTP in several copies. Measured on a real
HTTP server with a stand-in for Paperless (the peak of single runs on macOS, above about 125 MB
when idle): a 10 MiB file took about 70 MB, four at once about 145 MB, and a 100 MiB file 640 MB on
a fresh server and up to about 890 MB after an earlier upload. Nothing limits how many uploads run at once, so
give a container the memory for the limit you set.

**Uploads by path.** With `PAPERLESS_MCP_UPLOAD_DIRS` set, the two tools also take a `path` on
the machine the server runs on, which is what you want with Claude Code or Claude Desktop: the
model names the file instead of encoding it.

- Directories are absolute, comma-separated or a JSON array. `/`, your home directory itself,
  `/proc`, `/sys`, `/dev` and `/run` are refused as entries.
- Only the stdio transport ever offers the `path` parameter, and only when this is set; over
  HTTP callers send base64 content.
- Symlinks are resolved and must end inside these directories; only regular files with a single
  hard link are read, `..` in a path is refused, and files or folders whose name starts with a
  dot below an entry (`.ssh`, `.env`) are never read.
- The server runs these tools over stdio only, whatever `FASTMCP_TRANSPORT` says.

**Downloads.** `download_document` attaches the original or archive file up to
`PAPERLESS_MCP_MAX_DOWNLOAD_BYTES`; larger files are reported with their link only. Attached
files travel base64-encoded (a third larger) in one MCP message and cost the client context
tokens; the FastMCP client reads at most 1 MiB per HTTP event by default, so raise the limit only
if your clients allow it.

## Startup and logs

At startup the server checks the connection: a rejected token or a Paperless that does not speak
API version 10 stops it with a message on stderr (in Claude Code: `claude --debug` or the `/mcp`
details). An unreachable Paperless does not stop it; the tools report the problem until
Paperless is back.

Logs go to stderr (the server's own records as JSON lines). At INFO and above, the records of tool
calls hold no arguments, results or error messages, the API token and bearer tokens are not
logged, and an internal error in a tool is logged by type. Error messages of resources and prompts
and a failed startup check are logged and can quote a prompt's argument or Paperless's answer;
DEBUG can log arguments and document content. The full list is in the
[privacy policy](../PRIVACY.md#what-the-server-logs).
