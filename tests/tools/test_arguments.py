"""Every tool takes every argument it offers, and every value of an enum it offers (PLMCP-91).

The coverage table (docs/api-coverage.md) credits a tool only with what its tests make it send.
These scenarios call the tools with all their arguments at once, and once more for each value of
each enum, on the seeded fake; each call must succeed, so an argument that no longer reaches
Paperless fails here before it silently drops out of the table.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
from fakes.admin import BOOLEANS, CHOICES, MINIMUMS
from fakes.mail import FakeMailAccount, FakeMailRule
from fakes.paperless import FakePaperless, FakeSavedView, FakeState, FakeTask, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.domain.admin import CONFIG_READ_ONLY, CONFIG_SETTINGS
from mcp_server_paperless.domain.views import FilterRule
from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger
from mcp_server_paperless.services.filter_rules import SPECS

if TYPE_CHECKING:
    from fastmcp.client import FastMCPTransport

pytestmark = pytest.mark.anyio

type Json = dict[str, object]


def values(schema: Json, defs: Json) -> list[object]:
    """Every enum value the schema of an argument offers, also inside a list."""
    if "$ref" in schema:
        return values(cast("Json", defs[str(schema["$ref"]).rsplit("/", 1)[-1]]), defs)
    if "enum" in schema:
        return list(cast("list[object]", schema["enum"]))
    if schema.get("type") == "boolean":
        return [True, False]
    found: list[object] = []
    for key in ("anyOf", "oneOf"):
        for option in cast("list[object]", schema.get(key, [])):
            found += values(cast("Json", option), defs)
    if schema.get("type") == "array":
        found += [[v] for v in values(cast("Json", schema.get("items", {})), defs)]
    return found


FAILED: list[str] = []


@pytest.fixture(autouse=True)
def _no_failures() -> None:
    FAILED.clear()


type Prepare = Callable[[FakeState], None]


@dataclass(frozen=True)
class Session:
    """The server of the running test: one for all its calls, the instance reset for each."""

    fake: FakePaperless
    client: Client[FastMCPTransport]
    schemas: dict[str, Json]


SESSIONS: list[Session] = []


@pytest.fixture(autouse=True)
async def _session() -> AsyncIterator[None]:
    fake = admin()
    async with Client(serve(fake, toolsets=["all"])) as client:
        schemas = {t.name: t.input_schema for t in await client.list_tools()}
        SESSIONS.append(Session(fake, client, schemas))
        yield
    _ = SESSIONS.pop()


async def sweep_cases(tool: str, cases: list[tuple[Json, Prepare | None]]) -> None:
    """Call `tool` once per case on a fresh seeded instance (`prepare` adds what the case needs)."""
    session = SESSIONS[-1]
    confirms = "confirm" in cast("Json", session.schemas[tool].get("properties", {}))
    for arguments, prepare in cases:
        fresh = admin()  # every case starts from the seeded instance
        if prepare is not None:
            prepare(fresh.state)
        session.fake.state, session.fake.user = fresh.state, fresh.user
        given = {**arguments, "confirm": True} if confirms else arguments
        result = await session.client.call_tool(tool, given, raise_on_error=False)
        if result.is_error:
            FAILED.append(f"{tool} {given}: {result.content}")


async def sweep(
    tool: str,
    arguments: Json,
    vary: tuple[str, ...] | None = None,
    prepare: Prepare | None = None,
) -> None:
    """Call `tool` with `arguments`, then with each enum value of the arguments in `vary`."""
    schema = SESSIONS[-1].schemas[tool]
    defs = cast("Json", schema.get("$defs", {}))
    properties = cast("dict[str, Json]", schema.get("properties", {}))
    cases: list[tuple[Json, Prepare | None]] = [(arguments, prepare)]
    for name, prop in properties.items():
        if name != "confirm" and (vary is None or name in vary):
            cases += [({**arguments, name: value}, prepare) for value in values(prop, defs)]
    await sweep_cases(tool, cases)


def admin() -> FakePaperless:
    state = seeded()
    state.ai_enabled = True  # the ai toolset is offered
    return FakePaperless(state, user="admin", allow_writes=True)


async def test_taxonomy_is_listed_created_changed_and_deleted_of_every_kind() -> None:
    for kind in ("tag", "correspondent", "document_type", "storage_path", "custom_field"):
        await sweep("list_taxonomy", {"kind": kind, "name_contains": "e2e"}, ())
    for kind in ("user", "group", "saved_view"):
        await sweep("list_taxonomy", {"kind": kind, "name_contains": "e"}, ())
    settings = {
        "matching_algorithm": "any",
        "match": "word",
        "case_insensitive": True,
    }
    created = {
        "tag": {**settings, "colour": "#112233", "parent": "e2e-finance", "is_inbox_tag": True},
        "correspondent": settings,
        "document_type": settings,
        "storage_path": {**settings, "path": "{{ created_year }}/{{ title }}"},
    }
    for kind, extra in created.items():
        await sweep(
            "create_taxonomy_item",
            {"kind": kind, "name": "Sweep", **extra},
            ("matching_algorithm",),
        )
        await sweep(
            "update_taxonomy_item",
            {"kind": kind, "item": 2 if kind == "tag" else 1, "name": "Renamed", **extra},
            ("matching_algorithm",),
        )
        await sweep("delete_taxonomy_item", {"kind": kind, "item": 1}, ())
    for data_type in (
        "string",
        "url",
        "date",
        "boolean",
        "integer",
        "float",
        "monetary",
        "documentlink",
        "longtext",
    ):
        await sweep(
            "create_taxonomy_item",
            {"kind": "custom_field", "name": "Sweep", "data_type": data_type},
            (),
        )
    await sweep(
        "create_taxonomy_item",
        {
            "kind": "custom_field",
            "name": "Pick",
            "data_type": "select",
            "select_options": ["a", "b"],
        },
        (),
    )
    await sweep("update_taxonomy_item", {"kind": "tag", "item": 2, "clear_parent": True}, ())
    await sweep("delete_taxonomy_item", {"kind": "custom_field", "item": 1}, ())
    assert not FAILED, "\n".join(FAILED)


MAIL_ENUMS = (
    "attachment_type",
    "consumption_scope",
    "pdf_layout",
    "action",
    "assign_title_from",
    "assign_correspondent_from",
)


def bare(state: FakeState) -> None:
    """Documents without correspondent, type, path, ASN and date: every change is a change."""
    for document in state.documents:
        document.correspondent = document.document_type = document.storage_path = None
        document.archive_serial_number = None


def with_mail(state: FakeState) -> None:
    state.mail_accounts.append(
        FakeMailAccount(
            id=1,
            name="Invoices",
            imap_server="imap.example.com",
            imap_port=993,
            username="me@example.com",
            password="S3cret-Passw0rd!x",
            owner=2,
        )
    )
    state.mail_accounts.append(
        FakeMailAccount(
            id=2,
            name="Other",
            imap_server="imap.example.com",
            imap_port=993,
            username="you@example.com",
            password="S3cret-Passw0rd!x",
            owner=2,
        )
    )
    state.mail_rules.append(FakeMailRule(id=1, name="Bills", account=1, owner=2))


def with_other_mail(state: FakeState) -> None:
    """The same, with a rule whose enums are not the defaults: those values are sent as changes."""
    with_mail(state)
    rule = state.mail_rules[0]
    rule.attachment_type, rule.consumption_scope, rule.pdf_layout = "all_files", "eml", "text_html"
    rule.action, rule.assign_title_from, rule.assign_correspondent_from = (
        "delete",
        "filename",
        "name",
    )


async def test_documents_are_searched_changed_and_uploaded_with_every_argument() -> None:
    await sweep(
        "search_documents",
        {
            "title_contains": "e2e",
            "correspondent": 1,
            "document_type": 1,
            "storage_path": 1,
            "tags_all": [1],
            "tags_any": [1, 2],
            "tags_none": [3],
            "created_from": "2020-01-01",
            "created_to": "2030-01-01",
            "added_from": "2020-01-01",
            "added_to": "2030-01-01",
            "owner": "admin",
            "inbox": True,
            "page": 1,
            "page_size": 10,
        },
        ("inbox", "ordering"),
    )
    await sweep(
        "search_documents",
        {
            "query": "invoice",
            "has_correspondent": True,
            "has_document_type": True,
            "has_storage_path": True,
            "has_tags": True,
        },
    )
    await sweep("search_documents", {"more_like_id": 1})
    await sweep(
        "search_documents",
        {
            "has_correspondent": False,
            "has_document_type": False,
            "has_storage_path": False,
            "has_tags": False,
        },
    )
    await sweep(
        "update_document",
        {
            "document_id": 1,
            "title": "T",
            "correspondent": 1,
            "document_type": 1,
            "storage_path": 1,
            "created": "2026-01-02",
            "archive_serial_number": 7,
            "owner": "admin",
            "tags_add": [3],
            "tags_remove": [4],
            "create_missing_tags": True,
        },
        (),
        prepare=bare,
    )
    await sweep("update_document", {"document_id": 1, "tags_set": [1, 2]}, ())
    for field in (
        "correspondent",
        "document_type",
        "storage_path",
        "archive_serial_number",
        "owner",
    ):
        await sweep("update_document", {"document_id": 1, "clear": [field]}, ())
    assert not FAILED, "\n".join(FAILED)


async def test_files_are_uploaded_downloaded_and_shared_with_every_argument() -> None:
    pdf = "JVBERi0xLjQK"
    await sweep(
        "upload_document",
        {
            "content_base64": pdf,
            "filename": "a.pdf",
            "title": "T",
            "correspondent": 1,
            "document_type": 1,
            "storage_path": 1,
            "tags": [1],
            "created": "2026-01-02",
            "archive_serial_number": 999,
            "custom_fields": {"e2e-string": "x"},
            "wait_seconds": 0,
        },
    )
    await sweep(
        "bulk_download",
        {"document_ids": [1], "content": "both", "compression": "lzma", "follow_formatting": True},
    )
    await sweep(
        "create_share_link", {"document_id": 1, "expires_in_days": 3, "file_version": "original"}
    )
    await sweep("create_share_bundle", {"document_ids": [1]})
    assert not FAILED, "\n".join(FAILED)


async def test_permissions_and_bulk_changes_take_every_argument() -> None:
    for kind in ("document", "tag", "correspondent", "document_type", "storage_path"):
        await sweep(
            "set_permissions",
            {
                "kind": kind,
                "items": [1],
                "owner": "admin",
                "view_users": ["admin"],
                "view_groups": ["e2e-group"],
                "change_users": ["admin"],
                "change_groups": [1],
                "mode": "replace",
            },
            ("mode",),
        )
        await sweep("set_permissions", {"kind": kind, "items": [1], "clear_owner": True}, ())
    for kind in ("tag", "correspondent", "document_type", "storage_path"):
        await sweep(
            "bulk_edit_taxonomy", {"kind": kind, "items": [1], "operation": {"op": "delete"}}, ()
        )
        await sweep(
            "bulk_edit_taxonomy",
            {
                "kind": kind,
                "items": [1],
                "operation": {
                    "op": "set_permissions",
                    "owner": "admin",
                    "view_users": ["admin"],
                    "view_groups": ["e2e-group"],
                    "change_users": ["admin"],
                    "change_groups": ["e2e-group"],
                    "merge": False,
                },
            },
            (),
        )
    operations: list[Json] = [
        {"op": "add_tags", "tags": [1]},
        {"op": "remove_tags", "tags": [4]},
        {"op": "modify_tags", "add": [3], "remove": [4]},
        {"op": "set_correspondent", "correspondent": 1},
        {"op": "set_document_type", "document_type": 1},
        {"op": "set_storage_path", "storage_path": 1},
        {"op": "modify_custom_fields", "values": {"e2e-string": "x"}, "remove": ["e2e-integer"]},
        {"op": "delete"},
        {"op": "reprocess"},
    ]
    for operation in operations:
        await sweep(
            "bulk_edit_documents",
            {"document_ids": [1], "operation": operation, "dry_run": False},
            (),
        )
    assert not FAILED, "\n".join(FAILED)


async def test_the_administration_takes_every_argument() -> None:
    await sweep(
        "create_user",
        {
            "username": "sweeper",
            "password": "S3cret-Passw0rd!x",
            "email": "a@b.example",
            "first_name": "A",
            "last_name": "B",
            "is_active": True,
            "is_staff": True,
            "is_superuser": False,
            "groups": ["e2e-group"],
            "permissions": ["view_document"],
        },
        (),
    )
    await sweep(
        "update_user",
        {
            "user": "e2e-reader",
            "username": "renamed",
            "password": "S3cret-Passw0rd!x",
            "email": "a@b.example",
            "first_name": "A",
            "last_name": "B",
            "is_active": False,
            "is_staff": False,
            "is_superuser": True,
            "groups": [1],
            "permissions": ["view_document"],
        },
        (),
    )
    await sweep("create_group", {"name": "sweepers", "permissions": ["view_document"]}, ())
    await sweep(
        "update_group",
        {"group": "e2e-group", "name": "renamed", "permissions": ["view_document"]},
        (),
    )
    await sweep("list_users", {"name_contains": "e2e", "page": 1, "page_size": 10}, ())
    await sweep("list_groups", {"name_contains": "e2e", "page": 1, "page_size": 10}, ())
    await sweep("list_tasks", {"acknowledged": False, "page": 1, "page_size": 10})
    await sweep("run_task", {"task_type": "sanity_check"})
    config: Json = {}
    for name in CONFIG_SETTINGS:
        if name in CONFIG_READ_ONLY:
            continue
        if name in CHOICES:
            config[name] = CHOICES[name][0]
        elif name in BOOLEANS:
            config[name] = True
        elif name in MINIMUMS:
            config[name] = int(MINIMUMS[name]) + 1
        else:
            config[name] = "x"
    await sweep(
        "update_app_config",
        {
            "settings": config,
            "llm_api_key": "test-token-not-a-secret",
            "llm_embedding_api_key": "test-token-not-a-secret",
            "remote_ocr_api_key": "test-token-not-a-secret",
        },
        (),
    )
    for name, choices in CHOICES.items():
        for choice in choices:
            await sweep("update_app_config", {"settings": {name: choice}}, ())
    assert not FAILED, "\n".join(FAILED)


async def test_mail_takes_every_argument() -> None:
    account: Json = {
        "name": "Sweep",
        "imap_server": "imap.example.com",
        "imap_port": 143,
        "username": "new@example.com",
        "password": "S3cret-Passw0rd!x",
        "imap_security": "starttls",
        "character_set": "ISO-8859-1",
    }
    await sweep("create_mail_account", account)
    await sweep(
        "update_mail_account",
        {"account_id": 1, **account},
        prepare=with_mail,
    )
    rule: Json = {
        "name": "Sweep",
        "enabled": False,
        "order": 3,
        "folder": "Archive",
        "filter_from": "a@b",
        "filter_to": "c@d",
        "filter_subject": "s",
        "filter_body": "b",
        "filter_attachment_filename_include": "*.pdf",
        "filter_attachment_filename_exclude": "*.png",
        "maximum_age_days": 31,
        "action_parameter": "Done",
        "assign_tags": [1],
        "assign_document_type": 1,
        "assign_owner_from_rule": False,
        "stop_processing": True,
        "owner": "admin",
    }
    await sweep("create_mail_rule", {"account_id": 1, **rule, "action": "move"}, prepare=with_mail)
    await sweep(
        "update_mail_rule",
        {"rule_id": 1, "account_id": 2, **rule, "action": "move"},
        MAIL_ENUMS,
        prepare=with_mail,
    )
    await sweep(
        "update_mail_rule",
        {"rule_id": 1, "account_id": 2, **rule, "action": "move"},
        MAIL_ENUMS,
        prepare=with_other_mail,
    )
    chosen: Json = {"assign_correspondent_from": "selected", "assign_correspondent": 1}
    await sweep(
        "create_mail_rule", {"account_id": 1, "name": "Pick", **chosen}, (), prepare=with_mail
    )
    await sweep("update_mail_rule", {"rule_id": 1, **chosen}, (), prepare=with_mail)
    await sweep("update_mail_rule", {"rule_id": 1, "name": "Z"}, ("clear",), prepare=with_mail)
    await sweep("test_mail_account", {"account_id": 1}, (), prepare=with_mail)
    await sweep("process_mail_account", {"account_id": 1}, (), prepare=with_mail)
    await sweep("list_processed_mail", {}, ())
    assert not FAILED, "\n".join(FAILED)


TRIGGERS: list[Json] = [
    {
        "type": "consumption",
        "sources": ["consume_folder", "api_upload", "mail_fetch", "web_ui"],
        "path": "/in/*",
        "filename": "*.pdf",
        "mailrule": 1,
    },
    {
        "type": "added",
        "match": "m",
        "matching_algorithm": "any",
        "is_insensitive": True,
        "has_tags": [1],
        "has_all_tags": [1, 2],
        "has_not_tags": [3],
        "has_any_correspondents": [1],
        "has_not_correspondents": [1],
        "has_any_document_types": [1],
        "has_not_document_types": [1],
        "has_any_storage_paths": [1],
        "has_not_storage_paths": [1],
    },
    {
        "type": "added",
        "has_correspondent": 1,
        "has_document_type": 1,
        "has_storage_path": 1,
        "custom_field_query": '["e2e-string", "exact", "x"]',
    },
    {"type": "updated", "match": "m", "matching_algorithm": "regex"},
    {
        "type": "scheduled",
        "schedule_date_field": "added",
        "schedule_offset_days": 3,
        "schedule_is_recurring": True,
        "schedule_recurring_interval_days": 7,
    },
    {"type": "scheduled", "schedule_date_field": "created", "schedule_date_custom_field": 1},
]
ACTIONS: list[Json] = [
    {
        "type": "assignment",
        "assign_title": "{{ title }}",
        "assign_tags": [1],
        "assign_correspondent": 1,
        "assign_document_type": 1,
        "assign_storage_path": 1,
        "assign_owner": "admin",
        "assign_view_users": ["admin"],
        "assign_view_groups": [1],
        "assign_change_users": ["admin"],
        "assign_change_groups": [1],
        "assign_custom_fields": [1],
        "assign_custom_fields_values": {"1": "x"},
    },
    {
        "type": "removal",
        "remove_all_tags": True,
        "remove_tags": [1],
        "remove_all_correspondents": True,
        "remove_correspondents": [1],
        "remove_all_document_types": True,
        "remove_document_types": [1],
        "remove_all_storage_paths": True,
        "remove_storage_paths": [1],
        "remove_all_custom_fields": True,
        "remove_custom_fields": [1],
        "remove_all_owners": True,
        "remove_owners": ["admin"],
        "remove_all_permissions": True,
        "remove_view_users": ["admin"],
        "remove_view_groups": [1],
        "remove_change_users": ["admin"],
        "remove_change_groups": [1],
    },
    {
        "type": "email",
        "email": {"subject": "s", "body": "b", "to": "a@b.example", "include_document": True},
    },
    {
        "type": "webhook",
        "webhook": {
            "url": "https://hook.example/x",
            "use_params": True,
            "as_json": True,
            "params": {"a": "b"},
            "body": "x",
            "headers": {"A": "b"},
            "include_document": True,
        },
    },
]


def with_workflow(state: FakeState) -> None:
    state.workflows.append(
        Workflow(
            id=1,
            name="Filing",
            order=0,
            enabled=False,
            triggers=(WorkflowTrigger(id=1, type=2, settings={"filter_has_tags": [1]}),),
            actions=(WorkflowAction(id=2, type=1, settings={"assign_tags": [4]}),),
        )
    )


async def test_workflows_take_every_trigger_and_action_with_every_setting() -> None:
    for trigger in TRIGGERS:
        await sweep(
            "create_workflow",
            {"name": "Sweep", "enabled": False, "order": 2, "triggers": [trigger]},
            (),
        )
        await sweep(
            "update_workflow",
            {"workflow": 1, "name": "Z", "enabled": False, "order": 2, "triggers": [trigger]},
            (),
            prepare=with_workflow,
        )
    for action in ACTIONS:
        await sweep("create_workflow", {"name": "Sweep", "enabled": False, "actions": [action]}, ())
        await sweep(
            "update_workflow", {"workflow": 1, "actions": [action]}, (), prepare=with_workflow
        )
    await sweep("get_workflow", {"workflow": 1}, (), prepare=with_workflow)
    await sweep("list_workflows", {}, (), prepare=with_workflow)
    await sweep("delete_workflow", {"workflow": 1}, (), prepare=with_workflow)
    assert not FAILED, "\n".join(FAILED)


def with_view(rule_type: int, value: str | None) -> Callable[[FakeState], None]:
    def prepare(state: FakeState) -> None:
        state.saved_views.append(
            FakeSavedView(
                id=2,
                name="Sweep view",
                owner=2,
                filter_rules=(FilterRule(rule_type=rule_type, value=value),),
            )
        )

    return prepare


SAMPLES = {
    "text": "x",
    "int": "1",
    "bool": "true",
    "date": "2026-01-02",
    "ids": "1",
    "ref": "1",
    "json": '["e2e-string", "exact", "x"]',
}


async def test_saved_views_take_every_rule_type_and_are_run() -> None:
    created: list[tuple[Json, Prepare | None]] = []
    updated: list[tuple[Json, Prepare | None]] = []
    ran: list[tuple[Json, Prepare | None]] = []
    for number, spec in SPECS.items():
        value = SAMPLES[spec.kind]
        rules = [{"rule": number, "value": value}]
        created.append(
            (
                {
                    "name": "New",
                    "rules": rules,
                    "sort_field": "created",
                    "sort_reverse": True,
                    "page_size": 10,
                },
                None,
            )
        )
        updated.append(
            (
                {
                    "view": 1,
                    "rules": rules,
                    "sort_field": "added",
                    "sort_reverse": True,
                    "page_size": 10,
                },
                None,
            )
        )
        ran.append(({"view": 2, "page": 1, "page_size": 10}, with_view(number, value)))
    await sweep_cases("create_saved_view", created)
    await sweep_cases("update_saved_view", updated)
    await sweep_cases("run_saved_view", ran)
    await sweep("update_saved_view", {"view": 1, "clear_sort": True, "clear_page_size": True}, ())
    await sweep("list_saved_views", {}, ())
    await sweep("get_saved_view", {"view": 1}, ())
    await sweep("delete_saved_view", {"view": 1}, ())
    assert not FAILED, "\n".join(FAILED)


TASK = "9f1c0f0a-61d4-4c06-9c5e-6c7a4b1f3e21"


def with_pages(state: FakeState) -> None:
    for document in state.documents:
        document.page_count = 4
    state.email_enabled = True
    state.tasks.append(FakeTask(id=1, task_id=TASK, status="success"))


def with_trash(state: FakeState) -> None:
    with_pages(state)
    state.trash.append(state.documents.pop())  # document 2 is in the trash


async def test_updates_that_need_the_current_object_read_it_first() -> None:
    for kind in ("tag", "correspondent", "document_type", "storage_path"):
        await sweep(
            "update_taxonomy_item",
            {"kind": kind, "item": 2 if kind == "tag" else 1, "match": "only"},
            (),
        )
        await sweep(
            "update_taxonomy_item",
            {"kind": kind, "item": 2 if kind == "tag" else 1, "matching_algorithm": "regex"},
            (),
        )
    await sweep("update_taxonomy_item", {"kind": "custom_field", "item": 1, "name": "Renamed"}, ())
    await sweep(
        "update_taxonomy_item",
        {
            "kind": "custom_field",
            "item": "e2e-select",
            "select_options_add": ["c"],
            "select_options_rename": {"alpha": "Alpha"},
            "select_options_remove": ["beta"],
        },
        (),
    )
    assert not FAILED, "\n".join(FAILED)


async def test_documents_are_read_with_every_section_and_slice() -> None:
    await sweep("get_instance_overview", {}, ())
    await sweep(
        "get_document",
        {"document_id": 1, "include": ["notes", "history", "metadata", "versions", "suggestions"]},
        (),
    )
    await sweep("get_document_content", {"document_id": 1, "offset": 1, "max_chars": 10}, ())
    await sweep("get_document_thumbnail", {"document_id": 1}, ())
    await sweep("download_document", {"document_id": 1})
    await sweep("add_note", {"document_id": 1, "note": "n"}, ())
    await sweep("delete_note", {"document_id": 1, "note_id": 1}, ())
    await sweep("list_document_versions", {"document_id": 1}, ())
    await sweep("set_document_version_label", {"document_id": 1, "version_id": 1, "label": "L"}, ())
    await sweep(
        "upload_document_version",
        {
            "document_id": 1,
            "content_base64": "JVBERi0xLjQK",
            "filename": "v.pdf",
            "label": "L",
            "wait_seconds": 0,
        },
        (),
    )
    await sweep(
        "merge_documents_as_versions",
        {"root_document_id": 1, "source_document_ids": [2], "label": "L"},
        (),
    )
    await sweep("delete_document", {"document_id": 2}, ())
    await sweep("list_trash", {"page": 1, "page_size": 10}, ())
    await sweep("get_task", {"task_id": TASK, "wait_seconds": 0}, (), prepare=with_pages)
    await sweep("ask_paperless_ai", {"question": "q"}, ())
    await sweep("get_ai_suggestions", {"document_id": 1}, ())
    assert not FAILED, "\n".join(FAILED)


async def test_the_pdf_tools_take_every_option() -> None:
    await sweep(
        "rotate_documents",
        {"document_ids": [1], "degrees": 90, "wait_seconds": 0},
        prepare=with_pages,
    )
    await sweep(
        "merge_documents",
        {
            "document_ids": [1, 2],
            "metadata_document_id": 1,
            "delete_originals": True,
            "wait_seconds": 0,
        },
        (),
        prepare=with_pages,
    )
    await sweep(
        "split_document",
        {
            "document_id": 1,
            "parts": ["1-2", "3-4"],
            "output": "replace_original",
            "wait_seconds": 0,
        },
        (),
        prepare=with_pages,
    )
    await sweep(
        "split_document",
        {"document_id": 1, "parts": ["1-2", "3-4"], "output": "new_document", "wait_seconds": 0},
        (),
        prepare=with_pages,
    )
    await sweep(
        "edit_pdf",
        {"document_id": 1, "pages": [{"page": 1, "rotate": 90}, {"page": 2}], "wait_seconds": 0},
        prepare=with_pages,
    )
    await sweep(
        "delete_pages", {"document_id": 1, "pages": "1", "wait_seconds": 0}, prepare=with_pages
    )
    await sweep(
        "remove_document_password",
        {"document_id": 1, "password": "pw", "wait_seconds": 0},
        prepare=with_pages,
    )
    await sweep(
        "reprocess_documents", {"document_ids": [1], "remote_ocr": True, "wait_seconds": 0}, ()
    )
    await sweep(
        "email_documents",
        {
            "document_ids": [1],
            "recipients": ["a@b.example"],
            "subject": "s",
            "message": "m",
            "use_archive_version": False,
        },
        (),
        prepare=with_pages,
    )
    await sweep(
        "create_share_bundle",
        {"document_ids": [1], "expires_in_days": 2, "file_version": "archive"},
    )
    await sweep("list_share_links", {"document_id": 1, "page": 1, "page_size": 10}, ())
    await sweep("list_share_bundles", {"page": 1, "page_size": 10}, ())
    await sweep("restore_from_trash", {"document_ids": [2]}, (), prepare=with_trash)
    await sweep("empty_trash", {"all": True}, (), prepare=with_trash)
    await sweep("empty_trash", {"document_ids": [2]}, (), prepare=with_trash)
    assert not FAILED, "\n".join(FAILED)
