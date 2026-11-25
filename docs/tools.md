# Tool reference

<!-- Generated from the tool surface by `just docs-tools`. Do not edit by hand. -->

104 tools in 13 toolsets, and 4 prompts. The toolsets `core` and `ai` are offered by
default; the others are switched on with
[`PAPERLESS_MCP_TOOLSETS`](configuration.md#toolsets), which also says what each toolset is for.

**Kind** is what a tool tells the client about itself (its MCP annotations): `read` changes
nothing, `write` adds or changes data, `destructive` can remove or overwrite data. **confirm**
marks a tool with a [confirmation step](configuration.md#confirmation) for its risky changes:
without `confirm=true` such a call asks you in a dialog, or changes nothing and answers with what
it would do. **dry run** marks a tool that can preview its effect first.

## `core` (on by default)

| Tool | Kind | What it does |
|---|---|---|
| `get_instance_overview` | read | Describe the Paperless instance as the current user sees it. |
| `list_taxonomy` | read | List the tags, correspondents, document types, storage paths, custom fields, saved views, users or groups the current user may see, as compact rows with their ids. |
| `search_documents` | read | Find documents by full text, similarity or structured filters; all filters combine. |
| `get_document` | read | Read one document: names, dates, tags, readable custom fields, notes count and web_url. |
| `get_document_content` | read | Read the OCR text of a document in slices of max_chars characters (default 20000). |
| `get_document_thumbnail` | read | See a document as a small preview image (its first page), for clients that read images. |
| `get_document_pages` | read | See pages of a document as images large enough to read. |
| `download_document` | read | Get the original file of a document, or its archive version, as an embedded resource. |
| `get_task` | read | Follow a Paperless background task, such as an upload being processed, to its result. |
| `update_document` | write, confirm | Change a document's title, correspondent, type, storage path, dates, owner, tags or custom fields. |
| `add_note` | write | Add a note to a document, as the current Paperless user. |
| `delete_note` | destructive, confirm | Delete a note from a document for good; notes have no trash. |
| `delete_document` | destructive, confirm | Delete a document by moving it to the Paperless trash, from where it can be restored. |
| `upload_document` | write, confirm | Upload a file, sent as base64 content, to Paperless as a new document. |
| `create_taxonomy_item` | write | Create a tag, correspondent, document type, storage path or custom field. |
| `update_taxonomy_item` | write, confirm | Change a tag, correspondent, document type, storage path or custom field. |
| `delete_taxonomy_item` | destructive, confirm | Delete a tag, correspondent, document type, storage path or custom field for good. |
| `run_saved_view` | read | Find the documents of a saved view, as the Paperless web UI lists them. |

## `ai` (on by default)

| Tool | Kind | What it does |
|---|---|---|
| `ask_paperless_ai` | read | Ask Paperless's AI a question about your documents and get its complete answer. |
| `get_ai_suggestions` | read | Ask Paperless's language model to suggest a title, tags, correspondent, document type, storage path and dates for one document. |

## `admin`

| Tool | Kind | What it does |
|---|---|---|
| `get_user` | read | Read one user: names, e-mail, active, staff and superuser flags, groups and permissions. |
| `create_user` | write, confirm | Create a Paperless user. |
| `update_user` | destructive, confirm | Change a user; only what you pass changes and the saved password stays. |
| `delete_user` | destructive, confirm | Delete a user for good. |
| `get_group` | read | Read one group: its name and the permission codenames it grants to its members. |
| `create_group` | write, confirm | Create a group, optionally with the permissions it grants to its members. |
| `update_group` | destructive, confirm | Rename a group or replace the permissions it grants to its members. |
| `delete_group` | destructive, confirm | Delete a group for good; its members lose what it granted them. |
| `get_app_config` | read | Read the application configuration: the settings that have a value, OCR, barcodes, AI. |
| `update_app_config` | destructive, confirm | Change the application configuration: OCR, barcodes, title, AI. |
| `list_logs` | read | List the log files this instance keeps (paperless, mail, celery). |
| `get_log` | read | Read the newest lines of a log, oldest first, to find out why something failed. |
| `list_tasks` | read | List background tasks, newest first: uploads, mail fetches, sanity checks, training. |
| `acknowledge_tasks` | write | Mark tasks as seen, e.g. failed ones after you looked at them. |
| `run_task` | write, confirm | Start a background task by hand and return its task_id; follow it with get_task. |
| `bulk_download` | read | Get the files of several documents as one ZIP archive, attached as an embedded resource. |

## `bulk`

| Tool | Kind | What it does |
|---|---|---|
| `read_documents` | read | Read many documents, at most 100 per call, chosen by ids, search filters or a saved view. |
| `bulk_edit_documents` | destructive, confirm, dry run | Change many documents in one step: tags, correspondent, type, storage path, custom fields, trash or reprocess. |
| `update_documents` | destructive, confirm, dry run | Change many documents in one call, each with values of its own: title, dates, correspondent, type, storage path, archive serial number, owner, tags and custom fields. |
| `add_notes` | write, confirm, dry run | Add a note to many documents: the same text for all chosen ones, or a text per document. |
| `bulk_edit_taxonomy` | destructive, confirm, dry run | Delete many tags, correspondents, document types or storage paths, or set who owns them and who may view or change them. |
| `resolve_duplicates` | destructive, confirm, dry run | Keep one document and move its duplicates to the trash, or merge them into it as versions. |
| `upload_documents` (stdio with upload directories) | write, dry run | Upload several files, or the files of a directory, to Paperless, each as a new document. |

## `mail`

| Tool | Kind | What it does |
|---|---|---|
| `list_mail_accounts` | read | List the mail accounts Paperless fetches documents from. |
| `get_mail_account` | read | Read one mail account: server, port, security, username, type. |
| `create_mail_account` | write | Add an IMAP account that Paperless can fetch mail from. |
| `update_mail_account` | write, confirm | Change a mail account; only what you pass changes, and the saved password stays. |
| `delete_mail_account` | destructive, confirm | Delete a mail account together with its rules and their processed-mail records. |
| `test_mail_account` | write | Try the login of a saved mail account: Paperless connects to its server and signs in. |
| `process_mail_account` | destructive, confirm | Fetch the mail of an account now instead of at the next scheduled run (every 10 minutes). |
| `list_mail_rules` | read | List the mail rules: which mails of an account become documents, and what happens next. |
| `get_mail_rule` | read | Read one mail rule with all its settings and the names of what it assigns. |
| `create_mail_rule` | write, confirm | Create a rule that turns the attachments of matching mails into documents. |
| `update_mail_rule` | write, confirm | Change a mail rule; only what you pass changes, and what is already so is not written. |
| `delete_mail_rule` | destructive, confirm | Delete a mail rule and the records of the mail it processed. |
| `list_processed_mail` | read | List the mails Paperless handled, as compact rows. |
| `get_processed_mail` | read | Read one mail Paperless handled, with its whole error. |
| `explain_mail_import` | read | Explain why a mail was not imported, from settings, processed mail, tasks and logs. |
| `delete_processed_mail` | destructive, confirm, dry run | Delete records of processed mail, so that Paperless can fetch those mails again. |

## `pdf`

| Tool | Kind | What it does |
|---|---|---|
| `inspect_pdf` | read | See the pages of a PDF at a glance, to decide where to split, cut or rotate. |
| `insert_pages` | write, dry run | Insert pages of one document into a PDF, at any place. |
| `rotate_documents` | write, confirm, dry run | Rotate every page of PDF documents clockwise. |
| `merge_documents` | destructive, confirm, dry run | Merge PDF documents, in the order given, into one new document. |
| `split_document` | destructive, confirm, dry run | Split a PDF into one new document per page range. |
| `extract_pages` | destructive, confirm, dry run | Put pages of a PDF into one new document, and with remove take them out of the document. |
| `delete_pages` | destructive, confirm, dry run | Delete pages from a PDF by making a file without them. |
| `edit_pdf` | destructive, confirm, dry run | Build PDF files from the pages of one document: pick, reorder, rotate and group pages. |
| `remove_document_password` | destructive, confirm, dry run | Remove the password from an encrypted PDF. |
| `reprocess_documents` | destructive, confirm, dry run | Run OCR and archiving again for documents, one background task each. |

## `permissions`

| Tool | Kind | What it does |
|---|---|---|
| `set_permissions` | destructive, confirm | Share documents, taxonomy objects, mail accounts or mail rules, or hand them over. |
| `list_users` | read | List Paperless users as compact rows: id, username, name, active, superuser, group ids. |
| `list_groups` | read | List Paperless user groups as compact rows: id and name. |

## `raw`

| Tool | Kind | What it does |
|---|---|---|
| `paperless_api_get` | read | Read any path of the Paperless REST API (GET only) that the other tools do not cover. |

## `sharing`

| Tool | Kind | What it does |
|---|---|---|
| `list_share_links` | read | List the public share links of documents, as full URLs, with their expiry. |
| `create_share_link` | write, confirm | Publish the file of one document under a new public link and return its full URL. |
| `delete_share_link` | destructive, confirm | Delete a share link: its URL stops working for everyone who has it. |
| `list_share_bundles` | read | List the share-link bundles (one public URL for a ZIP archive of several documents). |
| `create_share_bundle` | write, confirm | Publish the files of several documents as one ZIP archive under a public link. |
| `rebuild_share_bundle` | write | Build the archive of a bundle again, for example after a failed build. |
| `delete_share_bundle` | destructive, confirm | Delete a bundle and its archive: its URL stops working for everyone who has it. |
| `email_documents` | destructive, confirm | Send the files of documents by e-mail through the mail server of Paperless. |

## `trash`

| Tool | Kind | What it does |
|---|---|---|
| `list_trash` | read | List what was deleted and can still be restored: documents and deleted versions. |
| `get_trash_entry` | read | Look into an entry of the trash: everything Paperless tells of it, and its text. |
| `get_trashed_file` | read | Get the thumbnail or a file of an entry of the trash, after a JSON report. |
| `restore_from_trash` | write | Take entries out of the trash: a document comes back whole, a version is re-attached. |
| `empty_trash` | destructive, confirm | Delete trash entries for good: documents, their files, text, notes and metadata are lost. |

## `versions`

| Tool | Kind | What it does |
|---|---|---|
| `list_document_versions` | read | List the file versions of a document, newest first. |
| `upload_document_version` | write, confirm | Upload a file, sent as base64 content, to a document as a new version. |
| `set_document_version_label` | write | Set or clear the label of one version of a document, the root version's too. |
| `delete_document_version` | destructive, confirm | Delete one version of a document by moving it to the Paperless trash. |
| `merge_documents_as_versions` | destructive, confirm | Merge documents into another document as its newest versions. |
| `correct_document_text` | destructive, confirm, dry run | Correct the OCR text of a document, so that search finds it: replace one exact passage, or the whole text. |

## `views`

| Tool | Kind | What it does |
|---|---|---|
| `list_saved_views` | read | List the saved views the current user may see, with their filter rules. |
| `get_saved_view` | read | Read one saved view with its filter rules, sort order and page size. |
| `create_saved_view` | write | Create a saved view: a named document filter that run_saved_view can run. |
| `update_saved_view` | write | Change a saved view; only the settings you name change. |
| `delete_saved_view` | destructive, confirm | Delete a saved view for good: it has no trash. |

## `workflows`

| Tool | Kind | What it does |
|---|---|---|
| `list_workflows` | read | List the workflows of Paperless with their triggers and actions, all or filtered. |
| `get_workflow` | read | Read one workflow with its triggers and actions, as list_workflows shows it. |
| `create_workflow` | write, confirm | Create a workflow with its triggers and actions. |
| `update_workflow` | write, confirm | Change a workflow; only the settings you name change. |
| `update_workflow_part` | destructive, confirm | Add, change or remove one trigger or action of a workflow; the others stay as they are. |
| `delete_workflow` | destructive, confirm | Delete a workflow for good: it has no trash. |
| `run_workflow` | destructive, confirm, dry run | Run a workflow with a manual trigger by hand on chosen documents, at most 100 per call. |

## Prompts

Guided multi-step tasks the client offers (in Claude Code as slash commands). Each one
proposes its changes and waits for a yes before it writes.

| Prompt | What it does |
|---|---|
| `annual_review` | Review one year of documents for a purpose such as a tax return and find what is missing. |
| `cleanup_taxonomy` | Find duplicate, unused and badly named tags, correspondents, types and storage paths and propose fixes. |
| `find_document` | Find one document in Paperless from a loose description and show where it is. |
| `triage_inbox` | Work through the documents in the Paperless inbox and propose how to file each one. |
