# Paperless API coverage

<!-- Generated from the tool tests by `just docs-coverage`. Do not edit by hand. -->

Every operation of the vendored schema (`docs/vendor/paperless/openapi.json`, Paperless-ngx
3.4.x) and the tools that cover it. The tool tests run every tool with its arguments; each call a
tool makes to a port is recorded and handed to the real adapter, which builds the request. A tool
covers an operation when one of its calls leads to that request. `paperless_api_get` (raw) never
counts. An operation without a tool has the task under which it is still open, or an exclusion the
owner approved, quoted with its date from the repository. A tool that only looks up names (tags,
correspondents) is named too; at most five tools are shown. The entries are in
[api-coverage.toml](api-coverage.toml); `tests/quality/test_api_coverage.py` fails when an
operation, field or enum value is neither covered nor listed, and when an open entry's task is
missing or done.

Limits. A field counts as sent only when some tool test makes a tool send it, so a field a tool
supports and no tool test uses is listed as open. The requests are built from recorded port calls
and answered with responses taken from the adapter tests, so a request that depends on a later
answer of Paperless may be missed. The adapter's own requests, and those of streamed bodies, are
recorded but not checked against the schema; the adapter tests check the others. Fields the
adapter sends by itself (the `[[internal]]` entries) are listed as open although a tool causes
them.

## Endpoints

| Operation | Covered by | Open task or exclusion |
|---|---|---|
| `POST /api/bulk_edit_objects/` | `bulk_edit_taxonomy` |  |
| `GET /api/config/` | `get_app_config`, `update_app_config` |  |
| `DELETE /api/config/{id}/` |  | PLMCP-83 |
| `GET /api/config/{id}/` |  | PLMCP-83 |
| `PATCH /api/config/{id}/` | `update_app_config` |  |
| `PUT /api/config/{id}/` |  | PLMCP-83 |
| `GET /api/correspondents/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_workflow` and 13 more |  |
| `POST /api/correspondents/` | `create_taxonomy_item` |  |
| `DELETE /api/correspondents/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/correspondents/{id}/` | `update_taxonomy_item` |  |
| `PATCH /api/correspondents/{id}/` | `set_permissions`, `update_taxonomy_item` |  |
| `PUT /api/correspondents/{id}/` |  | PLMCP-83 |
| `GET /api/custom_fields/` | `bulk_edit_documents`, `create_workflow`, `delete_taxonomy_item`, `get_document`, `list_taxonomy` and 5 more |  |
| `POST /api/custom_fields/` | `create_taxonomy_item` |  |
| `DELETE /api/custom_fields/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/custom_fields/{id}/` | `update_taxonomy_item` |  |
| `PATCH /api/custom_fields/{id}/` | `update_taxonomy_item` |  |
| `PUT /api/custom_fields/{id}/` |  | PLMCP-83 |
| `GET /api/document_types/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_workflow`, `delete_taxonomy_item` and 11 more |  |
| `POST /api/document_types/` | `create_taxonomy_item` |  |
| `DELETE /api/document_types/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/document_types/{id}/` | `update_taxonomy_item` |  |
| `PATCH /api/document_types/{id}/` | `set_permissions`, `update_taxonomy_item` |  |
| `PUT /api/document_types/{id}/` |  | PLMCP-83 |
| `GET /api/documents/` | `bulk_edit_documents`, `create_share_bundle`, `create_share_link`, `email_documents`, `get_document` and 7 more |  |
| `POST /api/documents/bulk_download/` | `bulk_download` |  |
| `POST /api/documents/bulk_edit/` | `bulk_edit_documents` |  |
| `POST /api/documents/chat/` | `ask_paperless_ai` |  |
| `POST /api/documents/delete/` |  | PLMCP-80 |
| `POST /api/documents/edit_pdf/` | `delete_pages`, `edit_pdf`, `split_document` |  |
| `POST /api/documents/email/` | `email_documents` |  |
| `POST /api/documents/merge/` | `merge_documents` |  |
| `POST /api/documents/merge_as_versions/` | `merge_documents_as_versions` |  |
| `GET /api/documents/next_asn/` |  | PLMCP-83 |
| `POST /api/documents/post_document/` | `upload_document` |  |
| `POST /api/documents/remove_password/` | `remove_document_password` |  |
| `POST /api/documents/reprocess/` | `reprocess_documents` |  |
| `POST /api/documents/rotate/` | `rotate_documents` |  |
| `POST /api/documents/selection_data/` |  | PLMCP-83 |
| `DELETE /api/documents/{id}/` | `delete_document` |  |
| `GET /api/documents/{id}/` | `delete_document`, `delete_document_version`, `delete_note`, `delete_pages`, `edit_pdf` and 11 more |  |
| `PATCH /api/documents/{id}/` | `set_permissions`, `update_document` |  |
| `PUT /api/documents/{id}/` |  | PLMCP-83 |
| `GET /api/documents/{id}/ai_suggestions/` | `get_ai_suggestions` |  |
| `GET /api/documents/{id}/download/` | `download_document` |  |
| `POST /api/documents/{id}/email/` |  | PLMCP-83 |
| `GET /api/documents/{id}/history/` | `get_document` |  |
| `GET /api/documents/{id}/metadata/` | `download_document`, `get_document` |  |
| `DELETE /api/documents/{id}/notes/` | `delete_note` |  |
| `GET /api/documents/{id}/notes/` | `delete_note`, `get_document` |  |
| `POST /api/documents/{id}/notes/` | `add_note` |  |
| `GET /api/documents/{id}/preview/` |  | PLMCP-83 |
| `GET /api/documents/{id}/root/` |  | PLMCP-83 |
| `GET /api/documents/{id}/share_links/` |  | PLMCP-83 |
| `GET /api/documents/{id}/suggestions/` | `get_document` |  |
| `GET /api/documents/{id}/thumb/` | `get_document_thumbnail` |  |
| `POST /api/documents/{id}/update_version/` | `upload_document_version` |  |
| `DELETE /api/documents/{id}/versions/{version_id}/` | `delete_document_version` |  |
| `PATCH /api/documents/{id}/versions/{version_id}/` | `set_document_version_label` |  |
| `GET /api/groups/` | `bulk_edit_taxonomy`, `create_user`, `create_workflow`, `delete_group`, `get_group` and 6 more |  |
| `POST /api/groups/` | `create_group` |  |
| `DELETE /api/groups/{id}/` | `delete_group` |  |
| `GET /api/groups/{id}/` | `delete_group`, `get_group`, `update_group` |  |
| `PATCH /api/groups/{id}/` | `update_group` |  |
| `PUT /api/groups/{id}/` |  | PLMCP-83 |
| `GET /api/logs/` | `list_logs` |  |
| `GET /api/logs/{id}/` | `get_log` |  |
| `GET /api/mail_accounts/` | `list_mail_accounts` |  |
| `POST /api/mail_accounts/` | `create_mail_account` |  |
| `POST /api/mail_accounts/test/` | `test_mail_account` |  |
| `DELETE /api/mail_accounts/{id}/` | `delete_mail_account` |  |
| `GET /api/mail_accounts/{id}/` | `create_mail_rule`, `delete_mail_account`, `delete_mail_rule`, `get_mail_account`, `process_mail_account` and 3 more |  |
| `PATCH /api/mail_accounts/{id}/` | `update_mail_account` |  |
| `PUT /api/mail_accounts/{id}/` |  | PLMCP-81 |
| `POST /api/mail_accounts/{id}/process/` | `process_mail_account` |  |
| `GET /api/mail_rules/` | `delete_mail_account`, `list_mail_rules`, `process_mail_account` |  |
| `POST /api/mail_rules/` | `create_mail_rule` |  |
| `DELETE /api/mail_rules/{id}/` | `delete_mail_rule` |  |
| `GET /api/mail_rules/{id}/` | `delete_mail_rule`, `get_mail_rule`, `update_mail_rule` |  |
| `PATCH /api/mail_rules/{id}/` | `update_mail_rule` |  |
| `PUT /api/mail_rules/{id}/` |  | PLMCP-81 |
| `GET /api/oauth/callback/` |  | PLMCP-81 |
| `GET /api/processed_mail/` | `list_processed_mail` |  |
| `POST /api/processed_mail/bulk_delete/` |  | PLMCP-81 |
| `GET /api/processed_mail/{id}/` |  | PLMCP-81 |
| `GET /api/profile/` |  | PLMCP-83 |
| `PATCH /api/profile/` |  | PLMCP-83 |
| `POST /api/profile/disconnect_social_account/` |  | PLMCP-83 |
| `POST /api/profile/generate_auth_token/` |  | PLMCP-83 |
| `GET /api/profile/social_account_providers/` |  | PLMCP-83 |
| `DELETE /api/profile/totp/` |  | PLMCP-83 |
| `GET /api/profile/totp/` |  | PLMCP-83 |
| `POST /api/profile/totp/` |  | PLMCP-83 |
| `GET /api/remote_version/` |  | PLMCP-83 |
| `GET /api/saved_views/` | `create_saved_view`, `delete_saved_view`, `get_saved_view`, `list_saved_views`, `list_taxonomy` and 2 more |  |
| `POST /api/saved_views/` | `create_saved_view` |  |
| `DELETE /api/saved_views/{id}/` | `delete_saved_view` |  |
| `GET /api/saved_views/{id}/` |  | PLMCP-88 |
| `PATCH /api/saved_views/{id}/` | `update_saved_view` |  |
| `PUT /api/saved_views/{id}/` |  | PLMCP-88 |
| `GET /api/search/` |  | PLMCP-83 |
| `GET /api/search/autocomplete/` |  | PLMCP-83 |
| `GET /api/share_link_bundles/` | `list_share_bundles` |  |
| `POST /api/share_link_bundles/` | `create_share_bundle` |  |
| `DELETE /api/share_link_bundles/{id}/` | `delete_share_bundle` |  |
| `GET /api/share_link_bundles/{id}/` | `delete_share_bundle`, `rebuild_share_bundle` |  |
| `POST /api/share_link_bundles/{id}/rebuild/` | `rebuild_share_bundle` |  |
| `GET /api/share_links/` | `list_share_links` |  |
| `POST /api/share_links/` | `create_share_link` |  |
| `DELETE /api/share_links/{id}/` | `delete_share_link` |  |
| `GET /api/share_links/{id}/` | `delete_share_link` |  |
| `GET /api/statistics/` | `get_instance_overview`, `process_mail_account`, `set_permissions` |  |
| `GET /api/status/` | `get_instance_overview`, `process_mail_account`, `set_permissions` |  |
| `GET /api/storage_paths/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_workflow`, `delete_taxonomy_item`, `get_document` and 7 more |  |
| `POST /api/storage_paths/` | `create_taxonomy_item` |  |
| `POST /api/storage_paths/test/` |  | PLMCP-83 |
| `DELETE /api/storage_paths/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/storage_paths/{id}/` | `update_taxonomy_item` |  |
| `PATCH /api/storage_paths/{id}/` | `set_permissions`, `update_taxonomy_item` |  |
| `PUT /api/storage_paths/{id}/` |  | PLMCP-83 |
| `GET /api/tags/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 18 more |  |
| `POST /api/tags/` | `create_taxonomy_item`, `update_document` |  |
| `DELETE /api/tags/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/tags/{id}/` | `update_taxonomy_item` |  |
| `PATCH /api/tags/{id}/` | `set_permissions`, `update_taxonomy_item` |  |
| `PUT /api/tags/{id}/` |  | PLMCP-83 |
| `GET /api/tasks/` | `bulk_edit_documents`, `delete_pages`, `edit_pdf`, `get_task`, `list_tasks` and 8 more |  |
| `POST /api/tasks/acknowledge/` | `acknowledge_tasks` |  |
| `GET /api/tasks/active/` |  | PLMCP-89 |
| `POST /api/tasks/run/` | `run_task` |  |
| `GET /api/tasks/status_counts/` |  | PLMCP-89 |
| `GET /api/tasks/summary/` |  | PLMCP-89 |
| `GET /api/tasks/{id}/` |  | PLMCP-89 |
| `POST /api/token/` |  | PLMCP-83 |
| `GET /api/trash/` | `empty_trash`, `list_trash`, `restore_from_trash` |  |
| `POST /api/trash/` | `empty_trash`, `restore_from_trash` |  |
| `GET /api/ui_settings/` | `delete_pages`, `delete_user`, `edit_pdf`, `email_documents`, `get_instance_overview` and 8 more |  |
| `POST /api/ui_settings/` |  | PLMCP-88 |
| `GET /api/users/` | `bulk_edit_taxonomy`, `create_mail_rule`, `create_workflow`, `delete_user`, `get_document` and 10 more |  |
| `POST /api/users/` | `create_user` |  |
| `DELETE /api/users/{id}/` | `delete_user` |  |
| `GET /api/users/{id}/` | `delete_user`, `get_user`, `update_user` |  |
| `PATCH /api/users/{id}/` | `update_user` |  |
| `PUT /api/users/{id}/` |  | PLMCP-83 |
| `POST /api/users/{id}/deactivate_totp/` |  | PLMCP-83 |
| `GET /api/workflow_actions/` |  | PLMCP-87 |
| `POST /api/workflow_actions/` |  | PLMCP-87 |
| `DELETE /api/workflow_actions/{id}/` |  | PLMCP-87 |
| `GET /api/workflow_actions/{id}/` |  | PLMCP-87 |
| `PATCH /api/workflow_actions/{id}/` |  | PLMCP-87 |
| `PUT /api/workflow_actions/{id}/` |  | PLMCP-87 |
| `GET /api/workflow_triggers/` |  | PLMCP-87 |
| `POST /api/workflow_triggers/` |  | PLMCP-87 |
| `DELETE /api/workflow_triggers/{id}/` |  | PLMCP-87 |
| `GET /api/workflow_triggers/{id}/` |  | PLMCP-87 |
| `PATCH /api/workflow_triggers/{id}/` |  | PLMCP-87 |
| `PUT /api/workflow_triggers/{id}/` |  | PLMCP-87 |
| `GET /api/workflows/` | `create_workflow`, `delete_workflow`, `get_workflow`, `list_workflows`, `update_workflow` |  |
| `POST /api/workflows/` | `create_workflow` |  |
| `DELETE /api/workflows/{id}/` | `delete_workflow` |  |
| `GET /api/workflows/{id}/` |  | PLMCP-87 |
| `PATCH /api/workflows/{id}/` | `update_workflow` |  |
| `PUT /api/workflows/{id}/` |  | PLMCP-87 |

## Query parameters, body fields and enum values no tool sends

Of the operations a tool covers: what the schema offers and no tool sends. `body:a.b` is a field of
an object, `body:a[].b` one of the objects in a list, and `body:method=rotate` one value of an
enum. Query parameters the generated schema omits (such as `version`) are listed by hand in the
TOML file.

| Operation | Fields | Open task or exclusion |
|---|---|---|
| `POST /api/bulk_edit_objects/` | `body:all`, `body:all=false`, `body:all=true`, `body:filters`, `body:merge=true` | PLMCP-80 |
| `PATCH /api/config/{id}/` | `body:ai_enabled=false`, `body:app_logo`, `body:barcode_enable_asn=false`, `body:barcode_enable_tag=false`, `body:barcode_enable_tiff_support=false`, `body:barcode_retain_split_pages=false`, `body:barcode_store_values=false`, `body:barcode_tag_split=false`, `body:barcodes_enabled=false`, `body:deskew=false`, `body:rotate_pages=false` | PLMCP-83 |
| `GET /api/correspondents/` | `query:full_perms=false`, `query:id`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `POST /api/correspondents/` | `body:is_insensitive=false`, `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `GET /api/correspondents/{id}/` | `query:full_perms`, `query:full_perms=false`, `query:full_perms=true` | PLMCP-83 |
| `PATCH /api/correspondents/{id}/` | `body:is_insensitive=false` | PLMCP-83 |
| `GET /api/custom_fields/` | `query:id`, `query:id__in`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `PATCH /api/custom_fields/{id}/` | `body:data_type`, `body:data_type=boolean`, `body:data_type=date`, `body:data_type=documentlink`, `body:data_type=float`, `body:data_type=integer`, `body:data_type=longtext`, `body:data_type=monetary`, `body:data_type=select`, `body:data_type=string`, `body:data_type=url` | PLMCP-83 |
| `GET /api/document_types/` | `query:full_perms=false`, `query:id`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `POST /api/document_types/` | `body:is_insensitive=false`, `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `GET /api/document_types/{id}/` | `query:full_perms`, `query:full_perms=false`, `query:full_perms=true` | PLMCP-83 |
| `PATCH /api/document_types/{id}/` | `body:is_insensitive=false` | PLMCP-83 |
| `GET /api/documents/` | `query:added__day`, `query:added__gt`, `query:added__gte`, `query:added__lt`, `query:added__lte`, `query:added__month`, `query:added__year`, `query:all`, `query:archive_serial_number__gte`, `query:archive_serial_number__isnull=false`, `query:archive_serial_number__lte`, `query:checksum__icontains`, `query:checksum__iendswith`, `query:checksum__iexact`, `query:checksum__istartswith`, `query:content__iendswith`, `query:content__iexact`, `query:content__istartswith`, `query:correspondent__name__icontains`, `query:correspondent__name__iendswith`, `query:correspondent__name__iexact`, `query:correspondent__name__istartswith`, `query:created__gt`, `query:created__gte`, `query:created__lt`, `query:created__lte`, `query:document_type__name__icontains`, `query:document_type__name__iendswith`, `query:document_type__name__iexact`, `query:document_type__name__istartswith`, `query:full_perms=false`, `query:has_custom_fields=false`, `query:has_duplicates=false`, `query:id`, `query:modified__date__gte`, `query:modified__date__lte`, `query:modified__day`, `query:modified__gt`, `query:modified__gte`, `query:modified__lt`, `query:modified__lte`, `query:modified__month`, `query:modified__year`, `query:original_filename__icontains`, `query:original_filename__iendswith`, `query:original_filename__iexact`, `query:original_filename__istartswith`, `query:owner__isnull=false`, `query:search`, `query:shared_by__id=false`, `query:shared_by__id=true`, `query:storage_path__name__icontains`, `query:storage_path__name__iendswith`, `query:storage_path__name__iexact`, `query:storage_path__name__istartswith`, `query:tags__id`, `query:tags__name__icontains`, `query:tags__name__iendswith`, `query:tags__name__iexact`, `query:tags__name__istartswith`, `query:title__iendswith`, `query:title__iexact`, `query:title__istartswith`, `query:title_content` | PLMCP-83 |
| `POST /api/documents/bulk_download/` | `body:all`, `body:all=false`, `body:all=true`, `body:excluded_documents`, `body:filters` | PLMCP-83 |
| `POST /api/documents/bulk_edit/` | `body:all`, `body:all=false`, `body:all=true`, `body:excluded_documents`, `body:filters`, `body:method=add_tag`, `body:method=delete_pages`, `body:method=edit_pdf`, `body:method=merge`, `body:method=remove_password`, `body:method=remove_tag`, `body:method=rotate`, `body:method=set_document_type`, `body:method=set_permissions`, `body:method=set_storage_path`, `body:method=split` | PLMCP-80 |
| `POST /api/documents/bulk_edit/` | `body:from_webui`, `body:from_webui=false`, `body:from_webui=true` | PLMCP-83 |
| `POST /api/documents/edit_pdf/` | `body:include_metadata`, `body:include_metadata=false`, `body:include_metadata=true`, `body:source_mode` | PLMCP-79 |
| `POST /api/documents/edit_pdf/` | `body:from_webui`, `body:from_webui=false`, `body:from_webui=true` | PLMCP-83 |
| `POST /api/documents/email/` | `body:use_archive_version=true` | PLMCP-83 |
| `POST /api/documents/merge/` | `body:archive_fallback`, `body:archive_fallback=false`, `body:archive_fallback=true`, `body:source_mode` | PLMCP-79 |
| `POST /api/documents/merge/` | `body:from_webui`, `body:from_webui=false`, `body:from_webui=true` | PLMCP-83 |
| `POST /api/documents/post_document/` | `body:from_webui`, `body:from_webui=false`, `body:from_webui=true` | PLMCP-83 |
| `POST /api/documents/remove_password/` | `body:include_metadata`, `body:include_metadata=false`, `body:include_metadata=true`, `body:source_mode` | PLMCP-79 |
| `POST /api/documents/remove_password/` | `body:from_webui`, `body:from_webui=false`, `body:from_webui=true` | PLMCP-83 |
| `POST /api/documents/reprocess/` | `body:all`, `body:all=false`, `body:all=true`, `body:excluded_documents`, `body:filters` | PLMCP-79 |
| `POST /api/documents/rotate/` | `body:all`, `body:all=false`, `body:all=true`, `body:excluded_documents`, `body:filters`, `body:source_mode` | PLMCP-79 |
| `POST /api/documents/rotate/` | `body:from_webui`, `body:from_webui=false`, `body:from_webui=true` | PLMCP-83 |
| `GET /api/documents/{id}/` | `query:version` | PLMCP-75 |
| `GET /api/documents/{id}/` | `query:full_perms`, `query:full_perms=false`, `query:full_perms=true` | PLMCP-83 |
| `PATCH /api/documents/{id}/` | `body:content`, `query:version` | PLMCP-75 |
| `PATCH /api/documents/{id}/` | `body:created_date`, `body:remove_inbox_tags`, `body:remove_inbox_tags=false`, `body:remove_inbox_tags=true` | PLMCP-83 |
| `GET /api/documents/{id}/download/` | `query:version` | PLMCP-76 |
| `GET /api/documents/{id}/download/` | `query:follow_formatting`, `query:follow_formatting=false`, `query:follow_formatting=true` | PLMCP-83 |
| `GET /api/documents/{id}/history/` | `query:page`, `query:page_size` | PLMCP-89 |
| `GET /api/documents/{id}/metadata/` | `query:version` | PLMCP-83 |
| `GET /api/documents/{id}/notes/` | `query:id` | PLMCP-83 |
| `POST /api/documents/{id}/notes/` | `query:id` | PLMCP-83 |
| `GET /api/documents/{id}/thumb/` | `query:version` | PLMCP-83 |
| `GET /api/groups/` | `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `POST /api/mail_accounts/` | `body:account_type`, `body:account_type=1`, `body:account_type=2`, `body:account_type=3`, `body:expiration`, `body:is_token`, `body:is_token=false`, `body:is_token=true` | PLMCP-81 |
| `POST /api/mail_accounts/` | `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `POST /api/mail_accounts/test/` | `body:account_type`, `body:account_type=1`, `body:account_type=2`, `body:account_type=3`, `body:character_set`, `body:expiration`, `body:imap_port`, `body:imap_security`, `body:imap_security=2`, `body:imap_security=3`, `body:imap_server`, `body:is_token`, `body:is_token=true`, `body:name`, `body:password`, `body:username` | PLMCP-81 |
| `POST /api/mail_accounts/test/` | `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `PATCH /api/mail_accounts/{id}/` | `body:account_type`, `body:account_type=1`, `body:account_type=2`, `body:account_type=3`, `body:expiration`, `body:imap_security=2`, `body:is_token`, `body:is_token=false`, `body:is_token=true` | PLMCP-81 |
| `PATCH /api/mail_accounts/{id}/` | `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `POST /api/mail_accounts/{id}/process/` | `body:account_type`, `body:account_type=1`, `body:account_type=2`, `body:account_type=3`, `body:expiration`, `body:imap_security=2`, `body:imap_security=3`, `body:is_token=true` | PLMCP-81 |
| `POST /api/mail_accounts/{id}/process/` | `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `POST /api/mail_rules/` | `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `PATCH /api/mail_rules/{id}/` | `body:assign_owner_from_rule=true`, `body:enabled=true`, `body:stop_processing=false` | PLMCP-81 |
| `PATCH /api/mail_rules/{id}/` | `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `GET /api/processed_mail/` | `query:ordering` | PLMCP-81 |
| `GET /api/saved_views/` | `query:full_perms`, `query:full_perms=false`, `query:full_perms=true`, `query:ordering` | PLMCP-88 |
| `POST /api/saved_views/` | `body:display_fields`, `body:display_mode`, `body:display_mode=largeCards`, `body:display_mode=smallCards`, `body:display_mode=table`, `body:icon`, `body:icon=archive`, `body:icon=bank`, `body:icon=basket`, `body:icon=bell`, `body:icon=bookmark`, `body:icon=boxes`, `body:icon=briefcase`, `body:icon=building`, `body:icon=calculator`, `body:icon=calendar`, `body:icon=camera`, `body:icon=card-checklist`, `body:icon=cash`, `body:icon=chat-left-text`, `body:icon=check-circle`, `body:icon=clipboard`, `body:icon=clock-history`, `body:icon=credit-card`, `body:icon=download`, `body:icon=envelope`, `body:icon=exclamation-triangle`, `body:icon=file-earmark`, `body:icon=file-earmark-check`, `body:icon=file-earmark-lock`, `body:icon=file-earmark-medical`, `body:icon=file-earmark-person`, `body:icon=file-earmark-spreadsheet`, `body:icon=file-text`, `body:icon=files`, `body:icon=folder`, `body:icon=funnel`, `body:icon=gear`, `body:icon=globe2`, `body:icon=hash`, `body:icon=heart`, `body:icon=house`, `body:icon=inbox`, `body:icon=journals`, `body:icon=list-task`, `body:icon=newspaper`, `body:icon=paperclip`, `body:icon=people`, `body:icon=person`, `body:icon=printer`, `body:icon=receipt`, `body:icon=safe`, `body:icon=search`, `body:icon=send`, `body:icon=shop`, `body:icon=stack`, `body:icon=stars`, `body:icon=tag`, `body:icon=tags`, `body:icon=telephone`, `body:icon=truck`, `body:icon=upc-scan`, `body:icon=wallet2`, `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-88 |
| `PATCH /api/saved_views/{id}/` | `body:display_fields`, `body:display_mode`, `body:display_mode=largeCards`, `body:display_mode=smallCards`, `body:display_mode=table`, `body:icon`, `body:icon=archive`, `body:icon=bank`, `body:icon=basket`, `body:icon=bell`, `body:icon=bookmark`, `body:icon=boxes`, `body:icon=briefcase`, `body:icon=building`, `body:icon=calculator`, `body:icon=calendar`, `body:icon=camera`, `body:icon=card-checklist`, `body:icon=cash`, `body:icon=chat-left-text`, `body:icon=check-circle`, `body:icon=clipboard`, `body:icon=clock-history`, `body:icon=credit-card`, `body:icon=download`, `body:icon=envelope`, `body:icon=exclamation-triangle`, `body:icon=file-earmark`, `body:icon=file-earmark-check`, `body:icon=file-earmark-lock`, `body:icon=file-earmark-medical`, `body:icon=file-earmark-person`, `body:icon=file-earmark-spreadsheet`, `body:icon=file-text`, `body:icon=files`, `body:icon=folder`, `body:icon=funnel`, `body:icon=gear`, `body:icon=globe2`, `body:icon=hash`, `body:icon=heart`, `body:icon=house`, `body:icon=inbox`, `body:icon=journals`, `body:icon=list-task`, `body:icon=newspaper`, `body:icon=paperclip`, `body:icon=people`, `body:icon=person`, `body:icon=printer`, `body:icon=receipt`, `body:icon=safe`, `body:icon=search`, `body:icon=send`, `body:icon=shop`, `body:icon=stack`, `body:icon=stars`, `body:icon=tag`, `body:icon=tags`, `body:icon=telephone`, `body:icon=truck`, `body:icon=upc-scan`, `body:icon=wallet2`, `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users`, `body:sort_reverse=false` | PLMCP-88 |
| `GET /api/share_link_bundles/` | `query:created__date__gt`, `query:created__date__gte`, `query:created__date__lt`, `query:created__date__lte`, `query:created__day`, `query:created__gt`, `query:created__gte`, `query:created__lt`, `query:created__lte`, `query:created__month`, `query:created__year`, `query:documents`, `query:expiration__date__gt`, `query:expiration__date__gte`, `query:expiration__date__lt`, `query:expiration__date__lte`, `query:expiration__day`, `query:expiration__gt`, `query:expiration__gte`, `query:expiration__lt`, `query:expiration__lte`, `query:expiration__month`, `query:expiration__year`, `query:ordering`, `query:status`, `query:status=failed`, `query:status=pending`, `query:status=processing`, `query:status=ready` | PLMCP-83 |
| `POST /api/share_link_bundles/{id}/rebuild/` | `body:expiration_days`, `body:file_version`, `body:file_version=archive`, `body:file_version=original` | PLMCP-83 |
| `GET /api/share_links/` | `query:created__date__gt`, `query:created__date__gte`, `query:created__date__lt`, `query:created__date__lte`, `query:created__day`, `query:created__gt`, `query:created__gte`, `query:created__lt`, `query:created__lte`, `query:created__month`, `query:created__year`, `query:expiration__date__gt`, `query:expiration__date__gte`, `query:expiration__date__lt`, `query:expiration__date__lte`, `query:expiration__day`, `query:expiration__gt`, `query:expiration__gte`, `query:expiration__lt`, `query:expiration__lte`, `query:expiration__month`, `query:expiration__year`, `query:ordering` | PLMCP-83 |
| `GET /api/storage_paths/` | `query:full_perms=false`, `query:id`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering`, `query:path__icontains`, `query:path__iendswith`, `query:path__iexact`, `query:path__istartswith` | PLMCP-83 |
| `POST /api/storage_paths/` | `body:is_insensitive=false`, `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `GET /api/storage_paths/{id}/` | `query:full_perms`, `query:full_perms=false`, `query:full_perms=true` | PLMCP-83 |
| `PATCH /api/storage_paths/{id}/` | `body:is_insensitive=false` | PLMCP-83 |
| `GET /api/tags/` | `query:full_perms=false`, `query:id`, `query:is_root`, `query:is_root=false`, `query:is_root=true`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `POST /api/tags/` | `body:is_inbox_tag=false`, `body:is_insensitive=false`, `body:owner`, `body:set_permissions`, `body:set_permissions.change`, `body:set_permissions.change.groups`, `body:set_permissions.change.users`, `body:set_permissions.view`, `body:set_permissions.view.groups`, `body:set_permissions.view.users` | PLMCP-83 |
| `GET /api/tags/{id}/` | `query:full_perms`, `query:full_perms=false`, `query:full_perms=true` | PLMCP-83 |
| `PATCH /api/tags/{id}/` | `body:is_inbox_tag=false`, `body:is_insensitive=false` | PLMCP-83 |
| `GET /api/tasks/` | `query:date_created_after`, `query:date_created_before`, `query:is_complete`, `query:is_complete=false`, `query:is_complete=true`, `query:name`, `query:ordering`, `query:owner`, `query:result`, `query:trigger_source`, `query:trigger_source=api_upload`, `query:trigger_source=email_consume`, `query:trigger_source=folder_consume`, `query:trigger_source=manual`, `query:trigger_source=scheduled`, `query:trigger_source=system`, `query:trigger_source=web_ui` | PLMCP-89 |
| `POST /api/tasks/acknowledge/` | `body:all`, `body:all=false`, `body:all=true` | PLMCP-89 |
| `POST /api/tasks/run/` | `body:task_type=apply_ai_suggestions`, `body:task_type=build_share_link`, `body:task_type=bulk_delete`, `body:task_type=bulk_update`, `body:task_type=check_workflows`, `body:task_type=consume_file`, `body:task_type=empty_trash`, `body:task_type=index_optimize`, `body:task_type=mail_fetch`, `body:task_type=reprocess_document` | PLMCP-89 |
| `GET /api/users/` | `query:ordering`, `query:username__iendswith`, `query:username__iexact`, `query:username__istartswith` | PLMCP-83 |
| `POST /api/users/` | `body:date_joined`, `body:is_active=false`, `body:is_staff=false`, `body:is_superuser=true` | PLMCP-83 |
| `PATCH /api/users/{id}/` | `body:date_joined`, `body:is_active=true`, `body:is_staff=false`, `body:is_superuser=false` | PLMCP-83 |
| `POST /api/workflows/` | `body:actions[].ai_create_missing`, `body:actions[].ai_create_missing=false`, `body:actions[].ai_create_missing=true`, `body:actions[].ai_overwrite_existing`, `body:actions[].ai_overwrite_existing=false`, `body:actions[].ai_overwrite_existing=true`, `body:actions[].ai_suggestion_fields`, `body:actions[].email.id`, `body:actions[].id`, `body:actions[].passwords`, `body:actions[].remove_all_correspondents=false`, `body:actions[].remove_all_custom_fields=false`, `body:actions[].remove_all_document_types=false`, `body:actions[].remove_all_owners=false`, `body:actions[].remove_all_permissions=false`, `body:actions[].remove_all_storage_paths=false`, `body:actions[].remove_all_tags=false`, `body:actions[].type=5`, `body:actions[].type=7`, `body:actions[].type=8`, `body:actions[].webhook.id`, `body:actions[].webhook.use_params=false`, `body:triggers[].id`, `body:triggers[].is_insensitive=false`, `body:triggers[].matching_algorithm=0`, `body:triggers[].matching_algorithm=2`, `body:triggers[].matching_algorithm=3`, `body:triggers[].matching_algorithm=5`, `body:triggers[].schedule_date_field=custom_field`, `body:triggers[].schedule_date_field=modified`, `body:triggers[].schedule_is_recurring=false` | PLMCP-87 |
| `PATCH /api/workflows/{id}/` | `body:actions[].ai_create_missing`, `body:actions[].ai_create_missing=false`, `body:actions[].ai_create_missing=true`, `body:actions[].ai_overwrite_existing`, `body:actions[].ai_overwrite_existing=false`, `body:actions[].ai_overwrite_existing=true`, `body:actions[].ai_suggestion_fields`, `body:actions[].email.id`, `body:actions[].email.include_document=false`, `body:actions[].id`, `body:actions[].passwords`, `body:actions[].remove_all_correspondents=false`, `body:actions[].remove_all_custom_fields=false`, `body:actions[].remove_all_document_types=false`, `body:actions[].remove_all_owners=false`, `body:actions[].remove_all_permissions=false`, `body:actions[].remove_all_storage_paths=false`, `body:actions[].remove_all_tags=false`, `body:actions[].type=5`, `body:actions[].type=6`, `body:actions[].type=7`, `body:actions[].type=8`, `body:actions[].webhook.id`, `body:actions[].webhook.use_params=false`, `body:enabled=true`, `body:triggers[].id`, `body:triggers[].is_insensitive=false`, `body:triggers[].matching_algorithm=0`, `body:triggers[].matching_algorithm=2`, `body:triggers[].matching_algorithm=3`, `body:triggers[].matching_algorithm=5`, `body:triggers[].schedule_date_field=custom_field`, `body:triggers[].schedule_date_field=modified`, `body:triggers[].schedule_is_recurring=false` | PLMCP-87 |

## Fields only a saved view reaches

These are sent only when `run_saved_view` runs a saved view that holds them; no tool takes them as
an argument.

| Operation | Fields |
|---|---|
| `GET /api/documents/` | `query:added__date__gt`, `query:added__date__gt=2026-01-02`, `query:added__date__gte=2026-01-02`, `query:added__date__lt`, `query:added__date__lt=2026-01-02`, `query:added__date__lte=2026-01-02`, `query:archive_serial_number`, `query:archive_serial_number=1`, `query:archive_serial_number__gt`, `query:archive_serial_number__gt=1`, `query:archive_serial_number__isnull`, `query:archive_serial_number__isnull=true`, `query:archive_serial_number__lt`, `query:archive_serial_number__lt=1`, `query:content__icontains`, `query:content__icontains=x`, `query:correspondent__id__in`, `query:correspondent__id__in=1`, `query:correspondent__id__none`, `query:correspondent__id__none=1`, `query:created__date__gt`, `query:created__date__gt=2026-01-02`, `query:created__date__gte=2026-01-02`, `query:created__date__lt`, `query:created__date__lt=2026-01-02`, `query:created__date__lte=2026-01-02`, `query:created__day`, `query:created__day=1`, `query:created__month`, `query:created__month=1`, `query:created__year`, `query:created__year=1`, `query:custom_field_query=["e2e-string", "exact", "x"]`, `query:custom_fields__icontains`, `query:custom_fields__icontains=x`, `query:custom_fields__id__all`, `query:custom_fields__id__all=1`, `query:custom_fields__id__in`, `query:custom_fields__id__in=1`, `query:custom_fields__id__none`, `query:custom_fields__id__none=1`, `query:document_type__id__in`, `query:document_type__id__in=1`, `query:document_type__id__none`, `query:document_type__id__none=1`, `query:has_custom_fields`, `query:has_custom_fields=true`, `query:has_duplicates`, `query:has_duplicates=true`, `query:mime_type`, `query:mime_type=x`, `query:modified__date__gt`, `query:modified__date__gt=2026-01-02`, `query:modified__date__lt`, `query:modified__date__lt=2026-01-02`, `query:owner__id=1`, `query:owner__id__in`, `query:owner__id__in=1`, `query:owner__id__none`, `query:owner__id__none=1`, `query:owner__isnull`, `query:owner__isnull=true`, `query:page=2`, `query:query=x`, `query:shared_by__id`, `query:shared_by__id=1`, `query:storage_path__id__in`, `query:storage_path__id__in=1`, `query:storage_path__id__none`, `query:storage_path__id__none=1`, `query:tags__id__all=2`, `query:tags__id__in=1`, `query:tags__id__none=1`, `query:text`, `query:text=x`, `query:title_search`, `query:title_search=x` |
