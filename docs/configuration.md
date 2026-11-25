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

Requires paperless-ngx 3.5.1 or later (API version 10). 3.5.1 is the oldest supported release and
the one the tests run against (a fast tier on every pull request, the whole suite after every
merge and nightly); the latest 3.x release is tested weekly. paperless-ngx
3.4.x, 3.5.0 and 2.x are not supported. Against an older release with API version 10 the server still
starts, but some tools fail or answer differently: it logs one warning, and `get_instance_overview`
says in `too_old` that Paperless must be updated. A version it cannot read as a release
(such as `unknown` when a proxy removes the `X-Version` header) gives no warning; if a proxy
removes `X-Api-Version` too, the server does not start against it.

## Server

| Variable | Meaning |
|---|---|
| `PAPERLESS_MCP_TOOLSETS` | Toolsets to offer, comma-separated, or `all` (default `core,ai`). See [Toolsets](#toolsets) |
| `PAPERLESS_MCP_READ_ONLY` | `true` to offer only tools that change nothing (default `false`). See [What the model can do](#what-the-model-can-do) |
| `PAPERLESS_MCP_REQUIRE_DIALOG` | `true` to let only an accepted dialog confirm; `confirm=true` then confirms nothing (default `false`). See [Only dialogs confirm](#only-dialogs-confirm) |
| `PAPERLESS_MCP_DISABLED_TOOLS` | Tools never offered, whatever the toolsets say, e.g. `ask_paperless_ai,get_ai_suggestions` |
| `PAPERLESS_MCP_UPLOAD_DIRS` | Directories the upload tools may read files from by path (default empty: uploading by path is off). See [Uploads and downloads](#uploads-and-downloads) |
| `PAPERLESS_MCP_DOWNLOAD_DIRS` | Directories `download_document` and `bulk_download` may save files into (stdio only; default empty: nothing is ever saved). See [Uploads and downloads](#uploads-and-downloads) |
| `PAPERLESS_MCP_MAX_UPLOAD_BYTES` | Largest file the upload tools take, in bytes (default 10485760 = 10 MiB, at most 104857600) |
| `PAPERLESS_MCP_MAX_DOWNLOAD_BYTES` | Largest file or part `download_document` and `bulk_download` attach, in bytes (default 524288, at most 33554432) |
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
  in Paperless or on this machine, from whichever toolsets are selected: searching, reading,
  listing, downloading. Downloads are then never saved, whatever `PAPERLESS_MCP_DOWNLOAD_DIRS` says.
  The prompts that need a writing tool are not offered either.
- **Disabled tools** (`PAPERLESS_MCP_DISABLED_TOOLS`). Takes single tools away by name.

Whatever is offered, the Paperless permissions of the token's user still apply, and tools that
can lose data or widen access have a [confirmation step](#confirmation). Every
tool also tells the client whether it only reads or may change or remove data (its MCP
annotations), which clients use for their own approval prompts. The [tool
reference](tools.md) lists the kind of every tool.

### Confirmation

A tool that can lose data or widen access, or that would upload a file Paperless already holds,
needs your consent for that, in one of two ways:

- **A dialog**, where your client can show one (it declares form elicitation of MCP). A call
  without `confirm` shows you what would happen and waits. Only if you accept does the call go
  on, and only for what you saw: if the target changes meanwhile you are asked again. If you
  decline, close the dialog, or do not answer within 10 minutes, nothing changes and the assistant is
  told not to try again on its own. The same holds if the connection drops before you answer.
- **`confirm=true`** with any other client. A call without it changes nothing and answers with
  what it would do; the assistant is told to describe that to you and to repeat the call with
  `confirm=true` once you agree.

The assistant can also pass `confirm=true` in its first call, when you already asked for exactly
this; no dialog is shown then. A client that declares dialogs but cannot show them to anybody (a
run without a person) is told "no" every time, so this is its only way. Dialogs need one server
process: with several behind one address, an answer that reaches another process changes nothing.

Which clients were seen to show the dialog: none yet. According to their documentation
[Claude Code](https://code.claude.com/docs/en/mcp) and
[VS Code](https://code.visualstudio.com/api/extension-guides/ai/mcp) show such dialogs.

### Only dialogs confirm

By default the assistant can pass `confirm=true` without asking you, also when text in a document
talked it into it (prompt injection). With `PAPERLESS_MCP_REQUIRE_DIALOG=true` that flag confirms
nothing: every tool with a `confirm` parameter asks in a dialog, and only an accepted one lets it go
on. It is for clients that show dialogs, and fails closed: nothing is changed, and the error names
the reason and this setting, if

- the client does not declare form elicitation, or the server cannot ask it on this connection,
- the dialog cannot be shown, is declined or closed, gets an answer that is not understood, or
  nobody answers within 10 minutes,
- the connection drops before the answer, or the target keeps changing while you are asked.

What needs no confirmation runs as before. The descriptions of the tools, the hints in their results
and the prompts are the same with and without the setting, so an assistant may still pass
`confirm=true`; you are asked then, too.

It is no proof of a human. The server sees only the client's answer: a client, a hook or a script can
accept dialogs automatically, and what the assistant itself can make the client do counts, too. Nor
does it stop changes that need no confirmation, such as a new title. Use
[read-only mode](#what-the-model-can-do) where nothing may be changed. Releases up to 0.3.3 ignore
the setting.

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
structured filters, `get_document`, `get_document_content` in slices, thumbnails, page images
(`get_document_pages`) and downloads),
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

Off by default. `read_documents` reads up to 100 documents a call, chosen by ids, by the filters of
`search_documents` or by a saved view, with the fields you name. At most 1000 documents may match:
more is refused with the count, and the first page names the id of each match, so the other tools
can take a set that was shown. Ids together with the filters or the view are read only while each of
them still matches. Text comes only when asked for, at most 20,000 characters a call, one document
after the other for at most 40 seconds; an answer has at most 100,000 characters, and what does not
fit, or whose text cannot be read, is named. Notes are refused to a user who may not view notes. `read_documents` only reads, so
read-only mode keeps it (decision-69 and decision-70 in `backlog/decisions/`).

Over stdio with upload directories the toolset also has `upload_documents`, see
[Uploads and downloads](#uploads-and-downloads). `bulk_edit_documents` changes many documents in one step (tags, correspondent,
type, storage path, custom fields, trash, reprocess) and `bulk_edit_taxonomy` deletes many tags,
correspondents, document types or storage paths or sets who owns and may see them. Both work in
three steps: `dry_run=true` previews how many objects change, a call without `confirm` asks you
in a [dialog](#confirmation) or is refused with a description of the effect, `confirm=true`
executes. Paperless checks every object
first: if one may not be changed, none is. Deleting taxonomy is final (it has no trash).

`bulk_edit_documents` chooses documents like `read_documents`: by ids, filters or a saved view. Every
document a call changes is named before (of other documents that mirrored links change at most 100),
and a filter or a view alone never acts: you accept the named
documents in a dialog, or, in a client without dialogs, a second call repeats the filter and names
their ids with `confirm=true` (refused if one no longer matches). Such a second call without a dialog
does not bind the current values: a tag someone added in between is not noticed. At most 100 documents change per
call; the same call goes on with the rest. If what a selection excludes is not there (a deleted tag
excludes nothing), the change is refused; a filter cannot reprocess more than 100 documents at once.
Paperless runs the change as a task: if that fails half way, or the answer is lost, the
documents are read again and the answer says which are as asked, which are not changed and which are
unknown (decision-71).

`update_documents` changes up to 100 documents with values of their own, every field `update_document`
knows. Everything that can be known is checked first, and one wrong item refuses the whole call, naming
all that are wrong. One confirmation covers the call: it names every document (the values of the first
20), every new owner, every new tag and the documents that mirrored links change. Then the documents are
read again and changed one after the other, for at most 40 seconds: a document whose values someone
changed meanwhile or that Paperless refuses is left as it is, the others go on, and the answer names what
became of each. Repeating the call goes on with the rest;
leave out documents whose owner was changed: one you may no longer see refuses the repeated call. A serial number can move to another document
if the one that releases it comes first; a swap takes two calls. `add_notes` adds a note to many documents,
chosen like those of `bulk_edit_documents`, or a note of its own to each (at most 100 a call). A note
whose text is there already is skipped, so it needs the permission to view notes (decision-73).

`resolve_duplicates` keeps one document and moves its duplicates to the trash or merges them into
it as versions (at most 20 a call). `search_documents` with `has_duplicates` finds such documents
and `get_document` with `include` `duplicates` lists the copies, also in the trash, and what differs.
Only documents that Paperless takes for duplicates of the kept one at that moment are touched, all or
none. From the trash they can be restored; a merge is final and drops their metadata and notes.
Documents the user cannot see are never compared (decision-51 in `backlog/decisions/`). Keeping a
document whose file Paperless cannot find is refused while a duplicate still has it, otherwise a
missing file is named; a merge is refused if a duplicate lacks its file or a file cannot be checked,
the trash if the kept document lacks its original and a duplicate cannot be checked. The check takes
at most 30 seconds (decision-63).

### `versions`

Off by default: `list_document_versions`, `upload_document_version`,
`set_document_version_label`, `delete_document_version`, `merge_documents_as_versions`,
`correct_document_text`. Works with the file versions of documents and their text.
`delete_document_version`, `merge_documents_as_versions` and `correct_document_text` need
`confirm`, and a merge removes the merged documents for good.

`correct_document_text` replaces one exact passage of the OCR text, or the whole text, of the
newest or of a named version. The file stays as it is.

- Its confirmation shows up to 300 characters of the old and of the new text, with hidden
  characters escaped; `dry_run=true` returns a longer change in full. That text reaches your trace
  backend if you export traces.
- It needs the `text_state` that `get_document_content` returned, and writes nothing if the text
  changed since. Paperless has no conditional write, so a change by somebody else in the moment of
  writing can still be overwritten. The text is read again afterwards, and the result says so if it
  is not the one that was sent.
- Reprocessing a document overwrites the correction. Search finds the text of the newest version
  only. According to the 3.5.1 source, Paperless keeps the old text only in its audit log, if that
  is on, and runs the workflows for updated documents.

### `views`

Off by default: `list_saved_views`, `get_saved_view`, `create_saved_view`, `update_saved_view`
and `delete_saved_view`, to read and maintain saved views: name, filter rules, sort order, page
size, and how the web UI shows them (icon, display mode, display fields; the last two can be put
back to its default). Running a view is `run_saved_view` in `core`.

### `trash`

Off by default: `list_trash`, `get_trash_entry`, `get_trashed_file`, `restore_from_trash`,
`empty_trash`. Lists what was deleted, shows what Paperless tells of it, restores it (a deleted
version is re-attached to its document) and empties the trash.

`list_trash` filters by title, document type, kind, day of deletion and ids. Paperless cannot filter
its trash, so the server looks at the first 1,000 entries in one request of at most 64 MiB (if that
is too large, slow or fails, at 100), and `truncated` says when there are more; `ids` also looks
beyond them. A page of the list, too, is at most 64 MiB, unpacked: a larger one is an error that
asks for a smaller `page_size`. Of a document in the trash Paperless shows the text, thumbnail and
file of its first version and no notes or custom field values: restore it to read the rest.
`get_document_thumbnail` does not show what is in the trash; `get_trashed_file` does.

`restore_from_trash` and `empty_trash` take `document_ids` or a `filter`. A filter never acts by
itself: it lists its matches, and only ids that were shown are acted on, by a dialog or a second
call. `empty_trash` never defaults to everything: it needs `document_ids`, a `filter` or `all=true`,
and a confirmation in every case, because it removes documents for good. Afterwards the trash is
read again, and the answer says of every entry what became of it.

### `pdf`

Off by default: `inspect_pdf`, `insert_pages`, `rotate_documents`, `merge_documents`,
`split_document`, `extract_pages`, `delete_pages`, `edit_pdf`, `remove_document_password`,
`reprocess_documents`. `inspect_pdf` only reads, so read-only mode keeps it: page count, sizes,
rotation and contact sheets of a PDF (limits under [Page images](#page-images)). `insert_pages` puts
pages of one document into another: this server builds the file and uploads it once, as a new version
or a new document. The others run Paperless's server-side PDF operations;
each returns the background tasks Paperless starts (`wait_seconds` waits for them)
and page ranges are checked against the page count of the file that is read first. `dry_run`
returns the plan (what is made, kept and lost) and starts nothing. Deleting originals, dropping
pages from what a document shows, showing an older file again and reprocessing need `confirm`.
Originals move to the Paperless trash last, by this server, once everything that was made is
checked; until then they stay and the result names the next calls. The same goes for the pages
`extract_pages` removes, and `split_document` gives each part its own title, type, correspondent,
date and tags. Where Paperless lists no task because a date custom field is copied, the new
documents are found by their file names, with a wait only and only if nothing else can be taken for
them. That needs the permissions `view_uisettings`, `view_paperlesstask` and `view_customfield` (to
see which custom fields are dates), and no other editing, merging, splitting or unlocking of this
user in the last hour that is not known to have made something else. A rotation is told by the new
version in the same way: also `view_logentry` is needed (and the audit log), because only the history
says who added a version. The versions of all earlier rotations of this user that ended in the last
hour must exist and be countable: one that failed, or whose version was deleted since, or more than
100 documents changed, or tasks listed, in that time leave the answer unknown.
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
`update_mail_rule`, `delete_mail_rule`, `list_processed_mail` (compact rows), `get_processed_mail`
(one mail with its whole error, in parts of 20,000 characters, and whether it would be fetched again
without its record) and `delete_processed_mail`.

- Passwords are write-only: they are accepted when an account is created or changed and are
  never returned, quoted or logged, nor is any OAuth token. Gmail and Outlook accounts are set up
  in the Paperless web UI, where the token is renewed as well; `update_mail_account` changes their
  other settings, such as the username, which is empty after the setup. Only a saved account can be
  tested.
- What a mail server answers to a login, which may repeat the password, is not shown, neither in
  the error of a processed mail nor in a log; all that follows it in that error goes with it, and
  so does the rest of a log record whose start was not read.
- Rules take tags, correspondent, document type and owner by name or id.
- `confirm` is needed to delete an account or a rule, to fetch an account now with
  `process_mail_account` (its rules can delete or move mail on the mail server, which cannot be
  undone), for a rule that is enabled and deletes or moves mail (when it is created, or when a
  change widens what it reaches), to point an account at another server, port or security
  without a new password (Paperless would send the saved password there) or to turn encryption
  off, and to hand a rule to another owner.
- `test_mail_account` logs in with the saved password and changes nothing; a failed login is a
  result, not an error.
- `delete_processed_mail` deletes 1 to 100 records, always behind `confirm` (`dry_run` shows the
  same): the confirmation names each mail and what follows, since Paperless can then fetch it
  again, import it a second time, and run the rule's action on it, which can delete it on the mail
  server. Each record is read again afterwards. It needs `view_processedmail` and
  `delete_processedmail`.
- `process_mail_account` only queues the fetch: follow it with `list_processed_mail` and the
  Paperless task list. While another fetch runs, Paperless skips it without an error.

### `workflows`

Off by default: `list_workflows`, `get_workflow`, `create_workflow`, `update_workflow`,
`update_workflow_part`, `delete_workflow`, `run_workflow`. Manages the workflows Paperless runs by itself, with triggers and actions as
nested objects and tags, correspondents, document types, storage paths, custom fields, users and
groups given by name. An enabled workflow keeps acting on every matching document, so
`create_workflow`, `update_workflow` and `update_workflow_part` need `confirm` when it moves documents to the trash,
e-mails them, calls a webhook, asks the AI, removes tags or other values, or changes owner or permissions
(disabled workflows and plain filing, such as tags and titles, need none), and `delete_workflow`
always does. Password removal is not written by these tools (it
is shown when read), webhook header values and passwords are never shown, and a setting that
does not belong to its trigger or action type is an error.

- `list_workflows` filters by enabled, by trigger type and by what a workflow uses (a tag,
  correspondent, document type, storage path, custom field, user, group or mail rule); `used_in`
  names the trigger or action and the setting. Names inside templates are not searched, nor parent
  and child tags that come along. `get_workflow` also reads one trigger or action by its id, with
  the workflows that hold it.
- `update_workflow_part` adds, changes, moves or removes one trigger or action and leaves the others
  as they are, hidden values and types this server does not write included. It reads the workflow
  right before and after the change, refuses it if someone else changed the workflow, and reports a
  difference instead of repeating anything. Losing webhook header values, URL
  credentials or passwords needs `confirm` even while the workflow is disabled, with
  `update_workflow` too.
- Webhook URLs are returned without the user name and password in them (a query string is
  returned; a URL in which they cannot be told apart is not); `shown_in_part` of the action says
  so. Written back as returned, on an action with the id it has now, the URL keeps what is stored;
  with an empty user name (`https://@host/path`) it loses them. Without the id, it is refused.
  A copy of it in another workflow is written without them, and the result says so.

`run_workflow` runs an enabled workflow with a manual trigger on up to 100 documents, chosen like those
of `bulk_edit_documents`; see `create_workflow` for manual triggers. Every run is confirmed:
every action with its step, with all e-mail recipients and webhook hosts, and every document is named before, also
tags that come along (parents of assigned ones, children of removed ones), and a
filter or a view alone never runs anything. With `apply_filters=false` the filters of the trigger are
left out; with them Paperless does not tell which documents it skips. If it fails on some documents the
answer names them, and a part of the actions may have run there. Whether e-mails, webhooks, password
removals and AI suggestions worked is not told by Paperless, and a call whose answer was lost is never
sent again. A run whose confirmation would be longer than 100,000 characters is refused, also with
`confirm` (decision-75). It needs the permissions to view workflows and to change documents, and
you have to own every document, or it has no owner.

### `admin`

Off by default. Administers the instance: users (`get_user`, `create_user`, `update_user`,
`delete_user`), groups (`get_group`, `create_group`, `update_group`, `delete_group`), the
application configuration (`get_app_config`, `update_app_config`), logs (`list_logs`,
`get_log`) and
`bulk_download`, which hands over the files of several documents as one ZIP archive: attached
within the download limit, in parts, or saved (see [Uploads and downloads](#uploads-and-downloads)).
`list_users` and `list_groups` of the `permissions` toolset find the users and
groups.

- Anything that gives or takes away access needs `confirm`, and the refusal names who is
  affected: creating a superuser or staff user or a user with a password, groups or permissions;
  changing a user's e-mail address, status, password, groups or permissions; deleting a user or a
  group; changing what a group grants.
- Every `update_app_config` needs `confirm` and lists each setting with its old and new value,
  because it applies to every user and the AI settings decide which service gets document text.
- Passwords and API keys are write-only: never returned, quoted or logged.
- Service URLs are returned without the user name and password in them (a query string is
  returned); `shown_in_part` names those that may have some. Writing one back as it was returned
  keeps what is stored; written with an empty user name (`https://@host/path`) it loses them.
- The account the server signs in with cannot be deleted, deactivated or stripped of its
  superuser status through these tools.
- Paperless decides who may do what: most tools need the matching Paperless permission, the
  logs are for staff users.
- Log lines come without the user name and password of the URLs in them (`note` says
  so).

### `tasks`

Off by default. The background tasks of Paperless: uploads, mail fetches, sanity checks, classifier
training and the rest. `list_tasks` lists them with every filter Paperless has (state, type, source,
seen or not, ended or not, owner, file name, result, time) and in any of its orders,
`get_task_overview` counts them, shows what runs and how each type did, `acknowledge_tasks` marks
them as seen, and `run_task` starts one. `get_task` of `core` follows one task by its `task_id`.

- `list_tasks`, `acknowledge_tasks` and `run_task` were in `admin` up to version 0.4: an operator who offered `admin` for them
  adds `tasks` (decision-93). User administration is not needed for them.
- Staff users see every task, others their own and those without an owner. Of what a task was given
  only the file name is shown, not the server path or the metadata of an upload.
- `name` finds part of the file's name, and also the words for type and source in the language of
  the server's Paperless user.
- `acknowledge_tasks` with ids needs no `confirm`; with `all_unacknowledged` it does, after saying
  how many tasks, which newest failures and whose they are, because there is no way back
  (`dry_run` shows the same). Tasks that come in meanwhile are marked too.
- In `get_task_overview` the filters apply to the counts only: Paperless offers none for the running
  tasks and the record by type. The record counts every user's tasks for who may view the system
  status, and says so.
- `run_task` starts the three types Paperless starts by hand, one per call. It needs `confirm` to
  train the classifier or update the AI index (which sends new and changed documents to the
  embedding service); a sanity check only reads and needs none. It is for superusers; the other tools need the matching Paperless
  permission.

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
  `params` and `body` are replaced with `[redacted]`, webhook URLs and log lines come without the
  user name and password of a URL (`note` says so), and an answer over 20 000 characters is cut
  with `truncated` true (narrow it with `page_size`, `fields` or filters).

The rules are decision-25 (`backlog/decisions/`).

## Uploads and downloads

**Uploads.** `upload_document` and `upload_document_version` take a file as base64 content. The
size limit is `PAPERLESS_MCP_MAX_UPLOAD_BYTES`, over stdio and over HTTP. Base64 content is a third
larger than the file and must be written out by the caller in one tool call, so real uploads are
far smaller. The size is checked on the text before anything is decoded, but the request body, its
text and the decoded bytes are in memory together, over HTTP in several copies. Measured on a real
HTTP server with a stand-in for Paperless (the peak of single runs on macOS, above about 125 MB
when idle), with a duplicate check of 5,000 documents with a version each and 500 trash entries: with
20 KB of text per entry a 10 MiB file took about 90 MB, four at once about 290 MB, and a 100 MiB file
640 MB on a fresh server and about 880 MB after an upload of 40 MiB; with 132 KB per entry, just
below the limit of 64 MiB for the trash, 205, 560 and 735 MB, and 140 MB for a file of 1 KiB. Nothing
limits how many uploads run at once, so give a container the memory for the limit you set.

**Duplicates.** Before anything is sent, both tools compare the file (its SHA-256) with what
Paperless already holds: the originals of documents, the versions of the 5,000 most recently changed
documents, and 500 trash entries. A file that is there already is sent only after a
[confirmation](#confirmation) that names where it is. Archive files, versions of trashed documents
and documents the Paperless user cannot see are not compared; the result lists every gap, and after
consumption it, and `get_task`, report the duplicates Paperless found or the document it rejected the
file for. If the check cannot run, because Paperless does not answer, because documents come and go while it
reads, or because it takes longer than 25 seconds, nothing is uploaded; no setting and
no `confirm` skips the check. The one exception is a trash whose entries with the text of their
documents are larger than 64 MiB: it is not checked, the rest is, and the result and the confirmation
say so. The rules are decision-47 and decision-52 (`backlog/decisions/`).

**Uploads by path.** With `PAPERLESS_MCP_UPLOAD_DIRS` set, the two tools also take a `path` on
the machine the server runs on, which is what you want with Claude Code or Claude Desktop: the
model names the file instead of encoding it.

- Directories are absolute, comma-separated or a JSON array. `/`, your home directory itself,
  `/proc`, `/sys`, `/dev` and `/run` are refused as entries.
- Only the stdio transport ever offers the `path` parameter, and only when this is set; over
  HTTP callers send base64 content. Over stdio without it, the tools tell the assistant that the
  operator can set it.
- Symlinks are resolved and must end inside these directories; only regular files with a single
  hard link are read, `..` in a path is refused, and files or folders whose name starts with a
  dot below an entry (`.ssh`, `.env`) are never read, also when zero-width or direction
  marks stand before the dot, nor any with control or line-break characters.
- With the toolset `bulk`, `upload_documents` uploads several files, or the files of a directory, in
  one call, each with its own report (uploaded, duplicate, refused or failed). It exists in this mode
  only. The assistant then sees the names in these directories; names that start with a dot are only
  counted. The rules above hold for every file, and a link to a directory is followed only inside
  these directories. Subdirectories are read only with `recursive`.
- One such call takes at most 25 files and 100 MiB together and starts no file but the first after 40
  seconds. A directory of more than 10,000 entries, or one that takes longer than 10 seconds to list,
  is refused; subdirectories more than 32 levels down are named, not read. What does not fit is named,
  and `next_after` goes on with the directory: it has to be passed unchanged, a file added before it
  meanwhile is missed (the number of files before it is said), and failed files are not repeated. After
  a timeout or an aborted call the report is lost: look at `list_tasks` (or the server's log) before
  repeating it. In reports a backslash in a name is doubled and characters nobody sees are escaped;
  `paths` and `directory` take paths in that form. It never sends a known duplicate: `upload_document` with `confirm` does, one
  by one. Equal files in two calls at once are not noticed until Paperless consumed the first. The
  rules are decision-50 (`backlog/decisions/`).
- The server runs these tools over stdio only, whatever `FASTMCP_TRANSPORT` says.

**Downloads.** `download_document` attaches the original or archive file up to
`PAPERLESS_MCP_MAX_DOWNLOAD_BYTES`, of the newest version or of the one named by `version_id`;
`bulk_download` (toolset `admin`) attaches a ZIP archive of the newest versions. Attached files
travel base64-encoded (a third larger) in one MCP message and cost the client context tokens; the
FastMCP client reads at most 1 MiB per HTTP event by default, so raise the limit only if your
clients allow it.

**Downloads in parts.** A larger file, up to 2 GiB, is read in parts over stdio and over HTTP: with
`offset=0` the tool answers the first part and a report, whose `next` names the arguments of the
next call, until `next_offset` is null. Each part is at most the download limit. Paperless serves no
ranges and the server keeps nothing between calls, so every part reads the file again from its start
(a ZIP archive is also built again, and read to its end), and parts of large files get slow.

- The report has the checksum (SHA-256) of the whole file, and every later part must bring it along.
  If the file changed in between, the call is refused with "The file changed since the first part;
  start again with offset=0". Compare the SHA-256 of the assembled file with `checksum` at the end:
  that, and not the server, proves that nothing was cut off or changed on the way.
- A document's file changes when it is reprocessed (its archive version). A ZIP archive also
  changes with a title, a new version, or anything else its names or files come from. On a
  Paperless with PostgreSQL there is one more case: the archive holds documents with the same
  creation date, and one of them was edited. Paperless sorts by that date only, and PostgreSQL may
  then return them in another order, so the same files come in another order (measured on 3.5.1: 4
  of 5 edits; never on SQLite). Read the parts again, or save the archive in one piece.

**Saving downloads.** With `PAPERLESS_MCP_DOWNLOAD_DIRS` set, the two tools also take `save_dir`
(and `save_as`): the file is saved on the machine the server runs on, and only a report comes back.

- Directories are named as for uploads by path, with the same entries refused. Only the stdio
  transport offers it, and not in read-only mode; over HTTP nothing is ever saved. Where it is
  offered, the two tools no longer count as read-only, so a client may ask before each call of them,
  also when nothing is saved.
- `save_dir` is an existing directory inside these directories; none is created. Symlinks are
  resolved and must end inside, `..` is refused, and so is every directory below an entry whose
  name starts with a dot or has characters nobody sees (zero-width, direction marks, line breaks). Every unusable directory gets the same answer.
- The name is the one Paperless proposes, or `save_as`. Its ending comes from the type Paperless
  detected (`.pdf`, `.docx`, `.eml`, …, and `.bin` for any type Paperless does not consume), so no
  title and no text of a document can make the server write a script, a program or a `CLAUDE.md`.
  Names that start with a dot are refused.
- An existing file is replaced only after a confirmation (a dialog where your client can show one,
  else `confirm=true`), which says what would be saved and names the file and the directory (where a
  link leads) in full; if either is longer than 254
  characters or holds characters that may not show, the replacement is refused. Opening the old file, a sync client, a backup or a `chmod`
  can count as a change of it: the call then asks again or ends with "changed meanwhile". And only a
  plain file: never a link, a file
  with a second name (hard link) or a device.
- Up to 2 GiB and 30 minutes; 256 MiB of the disk stay free, checked while the file arrives. Only a complete file gets its name, and
  it is compared with the size and checksum Paperless knows (a ZIP archive must be whole). It
  is readable for its owner only (mode 0600). A failed call leaves nothing, except that a
  call that is cancelled, or meets a disk error, just as the file gets its name can leave the
  finished file, complete: a disk error says so, a cancelled call has no answer to say it in. A killed
  server can leave a hidden `.mcp-….part`, which you may delete.
- Not on Windows: saving is refused there, as uploading by path is.

The rules are decision-45, decision-46 and, for replacing a file, decision-49 (`backlog/decisions/`).

### Page images

`get_document_pages` shows pages as images a model can read, from the original, the archive version
and any version of a document. PDFs and JPEG, PNG, TIFF and WebP images are drawn; other originals
(text, e-mail, Office) are shown through their archive version if they have one. Its limits are
fixed, and `PAPERLESS_MCP_MAX_DOWNLOAD_BYTES` does not apply, because the file goes to the renderer
and not to the assistant:

| Limit | Value |
|---|---|
| File | 100 MiB, and 120 seconds to download it |
| Image original | 100 megapixels per page |
| Images of one call | 5, together 720 KiB; the pages that did not fit are named |
| Long side of an image | 256 to 2576 pixels, 1568 by default; a region of a page can be enlarged to it |
| Renderer | 30 seconds and 1 GiB, one document at a time |
| Waiting | a call waits up to 30 seconds for the one before it, which can take 150 seconds (download and rendering), and is then told to try again |

Two kinds of image near 100 megapixels can end at the memory limit instead, with a message that says
so: on macOS a lossless WebP with a large file, and on Windows any WebP of roughly 60 megapixels or
more (estimated).

A progressive CMYK JPEG of more than 25 megapixels is drawn by PDFium, whose colours differ visibly
from those of other CMYK images (8 to 23 levels of 255 measured); the text is the same.

`inspect_pdf` (toolset `pdf`) reads PDFs and archive PDFs within the same limits and the same turn.
Its images are contact sheets of 12 numbered pages, at most 5 a call; a sheet that does not fit into
its share of the 720 KiB has a lower quality, and the report says so. After 20 seconds it stops and
names the pages that are left; a single page that takes longer than the 30 seconds ends the call
with an error that names it. The layout is of all pages asked for,
at most 100 runs of equal pages a call. Sizes are in
millimetres as PDFium shows the page, without a `UserUnit` of the file.

`insert_pages` (toolset `pdf`) builds its file in the same renderer, in the same turn. Both files
together, and the result, may have 100 MiB; downloading both has the 120 seconds, and building the
file and counting its pages the 30 seconds. A setting for uploads (`PAPERLESS_MCP_MAX_UPLOAD_BYTES`)
does not apply: the file comes from Paperless and goes back to it. Encrypted and damaged files are
refused, also PDFs that open without a password but restrict what may be done with them. Each
inserted page is looked for at its place in the saved file before anything is sent.

A page that does not fit alone is shown at a lower quality or smaller, and the report says so. Every
call downloads the file again. Text whose font is not embedded can look different where the system
has no such font, as in the container image. What the renderer may and may not do is in
[SECURITY.md](../SECURITY.md).

## Startup and logs

At startup the server checks the connection: a rejected token or a Paperless that does not speak
API version 10 stops it with a message on stderr (in Claude Code: `claude --debug` or the `/mcp`
details). An unreachable Paperless does not stop it; the tools report the problem until
Paperless is back.

Logs go to stderr (the server's own records as JSON lines). At INFO and above, the records of tool
calls, resources and prompts hold no arguments, results or error messages (the exceptions, such as
the name of an unknown tool and request paths, are in the policy), and the API token and bearer
tokens are not logged. Errors are logged by type and place in the code, without message or
traceback, also those that libraries log; a failed startup check is told by a text of this project
or by type. What a library writes into its own message stays: FastMCP's messages about a failed
sign-in through an identity provider quote some of its values. `DEBUG` logs the tracebacks, which can
quote arguments and what Paperless answered, document content included, because an error could not
be traced without them; arguments can also appear in other records. The answers to the client are
not logged as a whole, at any level. The full list is in the
[privacy policy](../PRIVACY.md#what-the-server-logs).
