# Paperless API coverage

<!-- Generated from the code and the tests by `just docs-coverage`. Do not edit by hand. -->

Every operation of the vendored schema (`docs/vendor/paperless/openapi.json`, Paperless-ngx
3.4.x) and the tools that cover it. A tool covers an operation when an adapter method it reaches
(through a service) sends that request in the adapter tests, which check every request against
the schema; a tool that only looks up names (tags, correspondents) is named too, and at most five
tools are shown. `paperless_api_get` (raw) never counts. An operation without a tool has the task
under which it is still open, or an exclusion the owner approved, quoted with its date from the
repository. The entries are in [api-coverage.toml](api-coverage.toml);
`tests/quality/test_api_coverage.py` fails when an operation or field is neither covered nor
listed, and when an open entry's task is missing or done.

## Endpoints

| Operation | Covered by | Open task or exclusion |
|---|---|---|
| `POST /api/bulk_edit_objects/` | `bulk_edit_taxonomy` |  |
| `GET /api/config/` | `get_app_config`, `update_app_config` |  |
| `DELETE /api/config/{id}/` |  | PLMCP-83 |
| `GET /api/config/{id}/` |  | PLMCP-83 |
| `PATCH /api/config/{id}/` | `update_app_config` |  |
| `PUT /api/config/{id}/` |  | PLMCP-83 |
| `GET /api/correspondents/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 25 more |  |
| `POST /api/correspondents/` | `create_taxonomy_item` |  |
| `DELETE /api/correspondents/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/correspondents/{id}/` | `update_taxonomy_item` |  |
| `PATCH /api/correspondents/{id}/` | `update_taxonomy_item` |  |
| `PUT /api/correspondents/{id}/` |  | PLMCP-83 |
| `GET /api/custom_fields/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 25 more |  |
| `POST /api/custom_fields/` | `create_taxonomy_item` |  |
| `DELETE /api/custom_fields/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/custom_fields/{id}/` | `update_taxonomy_item` |  |
| `PATCH /api/custom_fields/{id}/` | `update_taxonomy_item` |  |
| `PUT /api/custom_fields/{id}/` |  | PLMCP-83 |
| `GET /api/document_types/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 25 more |  |
| `POST /api/document_types/` | `create_taxonomy_item` |  |
| `DELETE /api/document_types/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/document_types/{id}/` |  | PLMCP-83 |
| `PATCH /api/document_types/{id}/` | `set_permissions` |  |
| `PUT /api/document_types/{id}/` |  | PLMCP-83 |
| `GET /api/documents/` | `bulk_edit_documents`, `create_share_bundle`, `create_share_link`, `email_documents`, `get_document` and 8 more |  |
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
| `GET /api/documents/{id}/history/` | `get_document`, `update_document` |  |
| `GET /api/documents/{id}/metadata/` | `download_document`, `get_document`, `update_document` |  |
| `DELETE /api/documents/{id}/notes/` | `delete_note` |  |
| `GET /api/documents/{id}/notes/` | `delete_note`, `get_document`, `update_document` |  |
| `POST /api/documents/{id}/notes/` | `add_note` |  |
| `GET /api/documents/{id}/preview/` |  | PLMCP-83 |
| `GET /api/documents/{id}/root/` |  | PLMCP-83 |
| `GET /api/documents/{id}/share_links/` |  | PLMCP-83 |
| `GET /api/documents/{id}/suggestions/` | `get_document`, `update_document` |  |
| `GET /api/documents/{id}/thumb/` | `get_document_thumbnail` |  |
| `POST /api/documents/{id}/update_version/` | `upload_document_version` |  |
| `DELETE /api/documents/{id}/versions/{version_id}/` | `delete_document_version` |  |
| `PATCH /api/documents/{id}/versions/{version_id}/` | `set_document_version_label` |  |
| `GET /api/groups/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 26 more |  |
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
| `GET /api/mail_rules/` | `delete_mail_account`, `list_mail_rules`, `process_mail_account`, `update_mail_rule` |  |
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
| `GET /api/saved_views/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 28 more |  |
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
| `GET /api/statistics/` | `create_mail_rule`, `get_instance_overview`, `process_mail_account`, `set_permissions` |  |
| `GET /api/status/` | `create_mail_rule`, `get_instance_overview`, `process_mail_account`, `set_permissions` |  |
| `GET /api/storage_paths/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 25 more |  |
| `POST /api/storage_paths/` | `create_taxonomy_item` |  |
| `POST /api/storage_paths/test/` |  | PLMCP-83 |
| `DELETE /api/storage_paths/{id}/` | `delete_taxonomy_item` |  |
| `GET /api/storage_paths/{id}/` |  | PLMCP-83 |
| `PATCH /api/storage_paths/{id}/` | `update_taxonomy_item` |  |
| `PUT /api/storage_paths/{id}/` |  | PLMCP-83 |
| `GET /api/tags/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 25 more |  |
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
| `GET /api/ui_settings/` | `create_mail_rule`, `delete_pages`, `delete_user`, `edit_pdf`, `email_documents` and 9 more |  |
| `POST /api/ui_settings/` |  | PLMCP-88 |
| `GET /api/users/` | `bulk_edit_documents`, `bulk_edit_taxonomy`, `create_mail_rule`, `create_saved_view`, `create_taxonomy_item` and 26 more |  |
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

## Query parameters and body fields no tool uses

Of the operations a tool covers: what the schema offers and no adapter test sends, so a field
the adapter supports without a test of its own is listed too. Query parameters the generated
schema omits (such as `version`) are listed by hand in the TOML file.

| Operation | Fields | Open task or exclusion |
|---|---|---|
| `POST /api/bulk_edit_objects/` | `body:all`, `body:filters` | PLMCP-80 |
| `PATCH /api/config/{id}/` | `body:ai_enabled`, `body:app_logo`, `body:archive_file_generation`, `body:barcode_asn_prefix`, `body:barcode_dpi`, `body:barcode_enable_asn`, `body:barcode_enable_tag`, `body:barcode_enable_tiff_support`, `body:barcode_max_pages`, `body:barcode_retain_split_pages`, `body:barcode_store_values`, `body:barcode_string`, `body:barcode_tag_mapping`, `body:barcode_tag_split`, `body:barcode_upscale`, `body:barcodes_enabled`, `body:color_conversion_strategy`, `body:deskew`, `body:image_dpi`, `body:language`, `body:llm_backend`, `body:llm_context_size`, `body:llm_embedding_api_key`, `body:llm_embedding_backend`, `body:llm_embedding_chunk_size`, `body:llm_embedding_endpoint`, `body:llm_embedding_model`, `body:llm_endpoint`, `body:llm_model`, `body:llm_output_language`, `body:llm_request_timeout`, `body:max_image_pixels`, `body:mode`, `body:output_type`, `body:remote_ocr_api_key`, `body:remote_ocr_endpoint`, `body:remote_ocr_engine`, `body:remote_ocr_mode`, `body:rotate_pages`, `body:rotate_pages_threshold`, `body:unpaper_clean`, `body:user_args` | PLMCP-83 |
| `GET /api/correspondents/` | `query:id`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `POST /api/correspondents/` | `body:is_insensitive`, `body:match`, `body:owner`, `body:set_permissions` | PLMCP-83 |
| `GET /api/correspondents/{id}/` | `query:full_perms` | PLMCP-83 |
| `PATCH /api/correspondents/{id}/` | `body:is_insensitive`, `body:owner`, `body:set_permissions`, `query:full_perms` | PLMCP-83 |
| `GET /api/custom_fields/` | `query:id`, `query:id__in`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `PATCH /api/custom_fields/{id}/` | `body:data_type` | PLMCP-83 |
| `GET /api/document_types/` | `query:id`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `POST /api/document_types/` | `body:is_insensitive`, `body:match`, `body:owner`, `body:set_permissions` | PLMCP-83 |
| `PATCH /api/document_types/{id}/` | `body:is_insensitive`, `body:match`, `body:matching_algorithm`, `body:name`, `body:set_permissions` | PLMCP-83 |
| `GET /api/documents/` | `query:added__day`, `query:added__gt`, `query:added__gte`, `query:added__lt`, `query:added__lte`, `query:added__month`, `query:added__year`, `query:all`, `query:archive_serial_number__gte`, `query:archive_serial_number__lte`, `query:checksum__icontains`, `query:checksum__iendswith`, `query:checksum__iexact`, `query:checksum__istartswith`, `query:content__iendswith`, `query:content__iexact`, `query:content__istartswith`, `query:correspondent__name__icontains`, `query:correspondent__name__iendswith`, `query:correspondent__name__iexact`, `query:correspondent__name__istartswith`, `query:created__gt`, `query:created__gte`, `query:created__lt`, `query:created__lte`, `query:document_type__name__icontains`, `query:document_type__name__iendswith`, `query:document_type__name__iexact`, `query:document_type__name__istartswith`, `query:id`, `query:modified__date__gte`, `query:modified__date__lte`, `query:modified__day`, `query:modified__gt`, `query:modified__gte`, `query:modified__lt`, `query:modified__lte`, `query:modified__month`, `query:modified__year`, `query:original_filename__icontains`, `query:original_filename__iendswith`, `query:original_filename__iexact`, `query:original_filename__istartswith`, `query:search`, `query:storage_path__name__icontains`, `query:storage_path__name__iendswith`, `query:storage_path__name__iexact`, `query:storage_path__name__istartswith`, `query:tags__id`, `query:tags__name__icontains`, `query:tags__name__iendswith`, `query:tags__name__iexact`, `query:tags__name__istartswith`, `query:title__iendswith`, `query:title__iexact`, `query:title__istartswith`, `query:title_content` | PLMCP-83 |
| `POST /api/documents/bulk_download/` | `body:all`, `body:excluded_documents`, `body:filters` | PLMCP-80 |
| `POST /api/documents/bulk_edit/` | `body:all`, `body:excluded_documents`, `body:filters`, `body:from_webui` | PLMCP-80 |
| `POST /api/documents/edit_pdf/` | `body:from_webui`, `body:include_metadata`, `body:source_mode` | PLMCP-79 |
| `POST /api/documents/merge/` | `body:archive_fallback`, `body:from_webui`, `body:source_mode` | PLMCP-79 |
| `POST /api/documents/post_document/` | `body:from_webui` | PLMCP-77 |
| `POST /api/documents/remove_password/` | `body:from_webui`, `body:include_metadata`, `body:source_mode` | PLMCP-79 |
| `POST /api/documents/reprocess/` | `body:all`, `body:excluded_documents`, `body:filters` | PLMCP-79 |
| `POST /api/documents/rotate/` | `body:all`, `body:excluded_documents`, `body:filters`, `body:from_webui`, `body:source_mode` | PLMCP-79 |
| `GET /api/documents/{id}/` | `query:version` | PLMCP-75 |
| `GET /api/documents/{id}/` | `query:full_perms` | PLMCP-83 |
| `PATCH /api/documents/{id}/` | `body:content`, `body:created_date`, `body:remove_inbox_tags`, `body:set_permissions`, `query:version` | PLMCP-75 |
| `GET /api/documents/{id}/download/` | `query:follow_formatting`, `query:version` | PLMCP-76 |
| `GET /api/documents/{id}/history/` | `query:page`, `query:page_size` | PLMCP-89 |
| `GET /api/documents/{id}/metadata/` | `query:version` | PLMCP-83 |
| `GET /api/documents/{id}/notes/` | `query:id` | PLMCP-83 |
| `POST /api/documents/{id}/notes/` | `query:id` | PLMCP-83 |
| `GET /api/documents/{id}/thumb/` | `query:version` | PLMCP-74 |
| `GET /api/groups/` | `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `POST /api/mail_accounts/` | `body:account_type`, `body:character_set`, `body:expiration`, `body:is_token`, `body:owner`, `body:set_permissions` | PLMCP-81 |
| `POST /api/mail_accounts/test/` | `body:account_type`, `body:expiration`, `body:owner`, `body:set_permissions` | PLMCP-81 |
| `PATCH /api/mail_accounts/{id}/` | `body:account_type`, `body:expiration`, `body:imap_port`, `body:imap_server`, `body:is_token`, `body:owner`, `body:set_permissions`, `body:username` | PLMCP-81 |
| `POST /api/mail_accounts/{id}/process/` | `body:account_type`, `body:expiration`, `body:owner`, `body:set_permissions` | PLMCP-81 |
| `POST /api/mail_rules/` | `body:filter_attachment_filename_exclude`, `body:filter_body`, `body:filter_subject`, `body:filter_to`, `body:set_permissions` | PLMCP-81 |
| `PATCH /api/mail_rules/{id}/` | `body:assign_owner_from_rule`, `body:enabled`, `body:filter_attachment_filename_include`, `body:folder`, `body:maximum_age`, `body:order`, `body:set_permissions`, `body:stop_processing` | PLMCP-81 |
| `GET /api/saved_views/` | `query:full_perms`, `query:ordering` | PLMCP-88 |
| `POST /api/saved_views/` | `body:display_fields`, `body:display_mode`, `body:icon`, `body:owner`, `body:set_permissions` | PLMCP-88 |
| `PATCH /api/saved_views/{id}/` | `body:display_fields`, `body:display_mode`, `body:icon`, `body:owner`, `body:set_permissions` | PLMCP-88 |
| `GET /api/share_link_bundles/` | `query:created__date__gt`, `query:created__date__gte`, `query:created__date__lt`, `query:created__date__lte`, `query:created__day`, `query:created__gt`, `query:created__gte`, `query:created__lt`, `query:created__lte`, `query:created__month`, `query:created__year`, `query:documents`, `query:expiration__date__gt`, `query:expiration__date__gte`, `query:expiration__date__lt`, `query:expiration__date__lte`, `query:expiration__day`, `query:expiration__gt`, `query:expiration__gte`, `query:expiration__lt`, `query:expiration__lte`, `query:expiration__month`, `query:expiration__year`, `query:ordering`, `query:status` | PLMCP-83 |
| `POST /api/share_link_bundles/{id}/rebuild/` | `body:expiration_days`, `body:file_version` | PLMCP-83 |
| `GET /api/share_links/` | `query:created__date__gt`, `query:created__date__gte`, `query:created__date__lt`, `query:created__date__lte`, `query:created__day`, `query:created__gt`, `query:created__gte`, `query:created__lt`, `query:created__lte`, `query:created__month`, `query:created__year`, `query:expiration__date__gt`, `query:expiration__date__gte`, `query:expiration__date__lt`, `query:expiration__date__lte`, `query:expiration__day`, `query:expiration__gt`, `query:expiration__gte`, `query:expiration__lt`, `query:expiration__lte`, `query:expiration__month`, `query:expiration__year`, `query:ordering` | PLMCP-83 |
| `GET /api/storage_paths/` | `query:id`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering`, `query:path__icontains`, `query:path__iendswith`, `query:path__iexact`, `query:path__istartswith` | PLMCP-83 |
| `POST /api/storage_paths/` | `body:is_insensitive`, `body:match`, `body:owner`, `body:set_permissions` | PLMCP-83 |
| `PATCH /api/storage_paths/{id}/` | `body:is_insensitive`, `body:match`, `body:owner`, `body:path`, `body:set_permissions`, `query:full_perms` | PLMCP-83 |
| `GET /api/tags/` | `query:id`, `query:is_root`, `query:name__icontains`, `query:name__iendswith`, `query:name__iexact`, `query:name__istartswith`, `query:ordering` | PLMCP-83 |
| `POST /api/tags/` | `body:owner`, `body:set_permissions` | PLMCP-83 |
| `GET /api/tags/{id}/` | `query:full_perms` | PLMCP-83 |
| `PATCH /api/tags/{id}/` | `body:is_insensitive` | PLMCP-83 |
| `GET /api/tasks/` | `query:date_created_before`, `query:is_complete`, `query:name`, `query:result`, `query:trigger_source` | PLMCP-89 |
| `POST /api/tasks/acknowledge/` | `body:all` | PLMCP-89 |
| `GET /api/users/` | `query:ordering`, `query:username__iendswith`, `query:username__iexact`, `query:username__istartswith` | PLMCP-83 |
| `POST /api/users/` | `body:date_joined` | PLMCP-83 |
| `PATCH /api/users/{id}/` | `body:date_joined`, `body:is_staff`, `body:is_superuser`, `body:last_name`, `body:password`, `body:user_permissions`, `body:username` | PLMCP-83 |
| `PATCH /api/workflows/{id}/` | `body:order` | PLMCP-87 |
