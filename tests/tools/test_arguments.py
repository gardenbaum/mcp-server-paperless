"""Every tool is called with every argument it offers, and with every enum or flag value (PLMCP-91).

The coverage table (docs/api-coverage.md) credits a tool only with what its tests make it send,
and the coverage test derives it from this file alone. Each scenario calls a tool with its
arguments, then once more for each value of the enums and flags it varies, on the seeded fake,
from a state where the value is a change (the services leave unchanged values out). A call must
succeed, and a tool that is not read-only must make a write call, on any `*_writes` port (the check
is per call: it does not tell which argument was written). That every tool and every argument of its
schema is in some scenario is checked by the coverage test (tests/quality/test_api_coverage.py),
which reads what the scenarios gave (`observation.GIVEN`) after it ran them; so each test of this
file passes alone and in any order.
"""

import hashlib
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

import observation
import pytest
from e2e.helpers import pdf
from fakes.admin import BOOLEANS, CHOICES, MINIMUMS
from fakes.mail import FakeMailAccount, FakeMailRule, FakeProcessedMail
from fakes.paperless import FakeDocument, FakePaperless, FakeSavedView, FakeState, FakeTask, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.domain.admin import CONFIG_READ_ONLY, CONFIG_SETTINGS
from mcp_server_paperless.domain.views import ICONS, FilterRule
from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger
from mcp_server_paperless.services.batch_uploads import cursor
from mcp_server_paperless.services.documents import text_state
from mcp_server_paperless.services.filter_rules import SPECS
from mcp_server_paperless.tools._shared import LOCAL_TOOLSETS

if TYPE_CHECKING:
    from pathlib import Path

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
SEED_STATE = text_state(1, 1, "E2E public invoice ACME 2026")


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
    reads: frozenset[str]  # the tools that only read


SESSIONS: list[Session] = []


@pytest.fixture(autouse=True)
async def _session() -> AsyncIterator[None]:
    async with serving():
        yield


@asynccontextmanager
async def serving(*, only: frozenset[str] | None = None, **how: object) -> AsyncGenerator[None]:
    """A server for `sweep`; its tools (`only` some of them) must have a scenario each."""
    fake = admin()
    before = observation.RECORDING
    observation.RECORDING = True  # the ports note their calls, to tell a write from none
    try:
        async with Client(serve(fake, toolsets=["all"], **how)) as client:  # pyright: ignore[reportArgumentType] -- keyword passthrough
            tools = [t for t in await client.list_tools() if only is None or t.name in only]
            reads = frozenset(
                t.name for t in tools if t.annotations and t.annotations.read_only_hint
            )
            schemas = {t.name: t.input_schema for t in tools}
            observation.TOOL_ARGUMENTS.update(
                {
                    name: frozenset(cast("Json", s.get("properties", {})))
                    for name, s in schemas.items()
                }
            )
            SESSIONS.append(Session(fake, client, schemas, reads))
            yield
    finally:
        _ = SESSIONS.pop() if SESSIONS else None
        observation.RECORDING = before
        if not before:  # a normal run: the notes of this test are of no use to any other
            observation.PORT_CALLS.clear()


async def step(tool: str, arguments: Json) -> Json:
    """One call on the current instance; a refusal, or a write tool that wrote nothing, fails."""
    session = SESSIONS[-1]
    confirms = "confirm" in cast("Json", session.schemas[tool].get("properties", {}))
    given = {**arguments, "confirm": True} if confirms else arguments
    observation.GIVEN.setdefault(tool, set()).update(given)
    start = len(observation.PORT_CALLS)
    result = await session.client.call_tool(tool, given, raise_on_error=False)
    if result.is_error:
        FAILED.append(f"{tool} {given}: {result.content}")
    elif (
        tool not in session.reads
        and given.get("dry_run") is not True
        and not any(call.field.endswith("_writes") for call in observation.PORT_CALLS[start:])
    ):
        FAILED.append(f"{tool} {given}: made no write call")
    return result.structured_content or {}


async def sweep_cases(tool: str, cases: list[tuple[Json, Prepare | None]]) -> None:
    """Call `tool` once per case on a fresh seeded instance (`prepare` adds what the case needs)."""
    session = SESSIONS[-1]
    for arguments, prepare in cases:
        fresh = admin()  # every case starts from the seeded instance
        if prepare is not None:
            prepare(fresh.state)
        session.fake.state, session.fake.user = fresh.state, fresh.user
        _ = await step(tool, arguments)


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


async def shown_first(tool: str, arguments: Json, prepare: Prepare) -> None:
    """Call a tool with an argument that only shows: the refusal names the ids (decision-55)."""
    session = SESSIONS[-1]
    fresh = admin()
    prepare(fresh.state)
    session.fake.state, session.fake.user = fresh.state, fresh.user
    observation.GIVEN.setdefault(tool, set()).update(arguments)
    result = await session.client.call_tool(tool, arguments, raise_on_error=False)
    if not (result.is_error and "document_ids=[2]" in str(result.content)):
        FAILED.append(f"{tool} {arguments}: {result.content}")


def admin() -> FakePaperless:
    state = seeded()
    state.ai_enabled = True  # the ai toolset is offered
    return FakePaperless(state, user="admin", allow_writes=True)


async def test_a_write_tool_that_writes_nothing_fails_its_scenario() -> None:
    # The title is the one the document has: the service sees no change and sends nothing.
    await sweep_cases(
        "update_document", [({"document_id": 1, "title": "e2e-public-invoice"}, None)]
    )
    # A read tool may of course send no write.
    await sweep_cases("get_document", [({"document_id": 1}, None)])

    assert len(FAILED) == 1
    assert "update_document" in FAILED[0]
    assert "no write" in FAILED[0]
    FAILED.clear()


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
        flags = ("matching_algorithm", "case_insensitive")
        flags += ("is_inbox_tag",) if kind == "tag" else ()
        await sweep("create_taxonomy_item", {"kind": kind, "name": "Sweep", **extra}, flags)
        await sweep(
            "update_taxonomy_item",
            {"kind": kind, "item": 2 if kind == "tag" else 1, "name": "Renamed", **extra},
            flags,
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
    # The inbox tag loses its flag; a tag that matches case-sensitively gets it back.
    await sweep(
        "update_taxonomy_item", {"kind": "tag", "item": 4, "name": "Inbox 2"}, ("is_inbox_tag",)
    )
    for kind in ("tag", "correspondent", "document_type", "storage_path"):
        await sweep(
            "update_taxonomy_item",
            {"kind": kind, "item": 1, "name": "Z"},
            ("case_insensitive",),
            prepare=sensitive,
        )
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


def sensitive(state: FakeState) -> None:
    """Every taxonomy object matches case-sensitively: ignoring case is then a change."""
    for objects in (state.tags, state.correspondents, state.document_types, state.storage_paths):
        for item in objects:
            item.case_insensitive = False


def owned(state: FakeState) -> None:
    """Everything has an owner, so that removing it is a change."""
    for objects in (
        state.documents,
        state.tags,
        state.correspondents,
        state.document_types,
        state.storage_paths,
    ):
        for item in objects:
            item.owner = 2


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


def with_processed_mail(state: FakeState) -> None:
    with_mail(state)
    state.processed_mail.append(FakeProcessedMail(id=1, rule=1, subject="Invoice", owner=2))


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
            "has_duplicates": True,
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
            "has_duplicates": False,
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
    await sweep(
        "update_document",
        {"document_id": 1, "tags_add": ["A brand new tag"], "create_missing_tags": True},
        (),
    )
    for field in (
        "correspondent",
        "document_type",
        "storage_path",
        "archive_serial_number",
        "owner",
    ):
        await sweep("update_document", {"document_id": 1, "clear": [field]}, (), prepare=owned)
    await sweep("update_document", {"document_id": 1, "custom_fields": {"e2e-string": "x"}}, ())
    await sweep("update_document", {"document_id": 1, "remove_custom_fields": ["e2e-string"]}, ())
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
    archive = await admin().download_documents(
        [1], content="archive", compression="none", follow_formatting=False, max_bytes=10_000
    )
    await sweep(
        "bulk_download",
        {
            "document_ids": [1],
            "offset": 1,
            "max_bytes": 5,
            "checksum": hashlib.sha256(archive.content).hexdigest(),
        },
        (),
    )
    await sweep(
        "create_share_link", {"document_id": 1, "expires_in_days": 3, "file_version": "original"}
    )
    await sweep("create_share_bundle", {"document_ids": [1]})
    assert not FAILED, "\n".join(FAILED)


async def test_several_files_are_uploaded_with_every_argument(tmp_path: Path) -> None:
    _ = (tmp_path / "a.pdf").write_bytes(pdf("a"))
    _ = (tmp_path / "b.pdf").write_bytes(pdf("b"))
    common: Json = {
        "correspondent": 1,
        "document_type": 1,
        "storage_path": 1,
        "tags": [1],
        "created": "2026-01-02",
        "custom_fields": {"e2e-string": "x"},
        "wait_seconds": 0,
    }
    async with serving(only=frozenset(LOCAL_TOOLSETS), stdio=True, upload_dirs=[str(tmp_path)]):
        await sweep("upload_documents", {**common, "paths": [str(tmp_path / "a.pdf")]}, ())
        await sweep("upload_documents", {**common, "directory": str(tmp_path)}, ("dry_run",))
        for recursive in (True, False):
            after = cursor(str(tmp_path / "a.pdf"), str(tmp_path), recursive=recursive)
            given = {"directory": str(tmp_path), "recursive": recursive, "after": after}
            await sweep("upload_documents", {**common, **given}, ())
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
        await sweep(
            "set_permissions", {"kind": kind, "items": [1], "clear_owner": True}, (), prepare=owned
        )
    for change in (
        {"owner": "e2e-reader", "view_users": ["e2e-reader"], "view_groups": ["e2e-group"]},
        {"change_users": ["e2e-reader"], "change_groups": [1], "mode": "replace"},
        {"clear_owner": True},
    ):
        await sweep("set_permissions", {"kind": "saved_view", "items": [1], **change}, ())
    for kind in ("mail_account", "mail_rule"):
        grants = {"view_users": ["admin"], "change_groups": [1], "owner": "e2e-reader"}
        await sweep(
            "set_permissions", {"kind": kind, "items": [1], **grants}, (), prepare=with_mail
        )
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
        {"op": "add_tags", "tags": [3]},
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
            prepare=bare,
        )
    by_filter: Json = {
        "document_ids": [1],
        "exclude_ids": [2],
        "filters": {"title_contains": "e2e"},
    }
    for chosen in (by_filter, {"document_ids": [1], "saved_view": "e2e-view"}):
        arguments: Json = {**chosen, "operation": {"op": "remove_tags", "tags": [4]}}
        await sweep("bulk_edit_documents", arguments, (), prepare=bare)
    assert not FAILED, "\n".join(FAILED)


async def test_many_documents_take_every_value_and_notes_every_choice() -> None:
    every: Json = {
        "document_id": 1,
        "title": "Renamed",
        "correspondent": 1,
        "document_type": 1,
        "storage_path": 1,
        "created": "2026-01-01",
        "archive_serial_number": 4242,
        "owner": 3,
        "tags_add": ["brand new"],
        "tags_remove": [4],
        "custom_fields": {"e2e-string": "x"},
        "remove_custom_fields": ["e2e-integer"],
    }
    documents: list[Json] = [every, {"document_id": 2, "clear": ["owner"], "tags_set": [1]}]
    arguments: Json = {"documents": documents, "create_missing_tags": True, "dry_run": False}
    await sweep("update_documents", arguments, ("dry_run",), prepare=bare)
    filters: Json = {"title_contains": "e2e"}
    for chosen in (
        {"document_ids": [1], "exclude_ids": [2], "filters": filters},
        {"document_ids": [1], "saved_view": "e2e-view"},
    ):
        await sweep("add_notes", {**chosen, "note": "New", "dry_run": False}, ("dry_run",))
    await sweep("add_notes", {"notes": [{"document_id": 1, "note": "New"}]}, ())
    assert not FAILED, "\n".join(FAILED)


def with_copies(state: FakeState) -> None:
    state.documents += [
        FakeDocument(id=n, title=f"Copy {n}", owner=2, original=b"%PDF same") for n in (80, 81)
    ]
    state.documents[-2].custom_fields = {8: [2]}  # a linked document, whose title is read
    state.trash.append(FakeDocument(id=82, title="Copy 82", owner=2, original=b"%PDF same"))


async def test_duplicates_are_listed_and_resolved_in_both_ways() -> None:
    arguments: Json = {"document_id": 80, "include": ["duplicates"]}
    await sweep("get_document", arguments, (), prepare=with_copies)
    for action in ("trash", "merge_as_versions"):
        await sweep(
            "resolve_duplicates",
            {
                "keep_document_id": 80,
                "duplicate_ids": [81],
                "action": action,
                "dry_run": False,
            },
            (),
            prepare=with_copies,
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
    await sweep(
        "list_tasks",
        {
            "is_complete": True,
            "owner": "admin",
            "name": "scan",
            "result": "error",
            "created_from": "2026-11-01T00:00:00Z",
            "created_to": "2026-11-30T00:00:00+01:00",
            "ordering": "-finished",
        },
    )
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
        "imap_server": "imap2.example.com",
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
    await sweep("list_processed_mail", {}, ("ordering",))
    await sweep(
        "explain_mail_import",
        {
            "account_id": 1,
            "rule_id": 1,
            "sender": "a",
            "recipient": "b",
            "subject": "Invoice",
            "received": "2026-11-25T10:00:00Z",
            "folder": "INBOX",
            "attachment_names": ["a.pdf"],
            "is_read": True,
            "is_flagged": False,
        },
        (),
        prepare=with_processed_mail,
    )
    await sweep(
        "get_processed_mail", {"mail_id": 1, "error_from": 0}, (), prepare=with_processed_mail
    )
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
    {"type": "manual", "filename": "*.pdf"},
    {"type": "scheduled", "schedule_date_field": "created", "schedule_date_custom_field": 1},
]
ACTIONS: list[Json] = [
    {
        "type": "apply AI suggestions",
        "ai_suggestion_fields": [
            *("title", "tags", "correspondent", "document_type", "storage_path", "created")
        ],
        "ai_create_missing": True,
        "ai_overwrite_existing": True,
    },
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
    state.workflow_object_id[0] = 10
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
    state.workflows.append(
        Workflow(
            id=2,
            name="By hand",
            order=1,
            enabled=True,
            triggers=(WorkflowTrigger(id=3, type=5),),
            actions=(WorkflowAction(id=4, type=1, settings={"assign_tags": [4]}),),
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
        built: Json = {"name": "Sweep", "enabled": False, "actions": [action]}
        await sweep("create_workflow", {**built, "triggers": [{"type": "manual"}]}, ())
        await sweep(
            "update_workflow", {"workflow": 1, "actions": [action]}, (), prepare=with_workflow
        )
    await sweep("get_workflow", {"workflow": 1}, (), prepare=with_workflow)
    await sweep("list_workflows", {}, (), prepare=with_workflow)
    await sweep("explain_workflow", {"workflow": 1, "document_id": 1}, (), prepare=with_workflow)
    await sweep("get_workflow", {"trigger_id": 1}, (), prepare=with_workflow)
    await sweep("get_workflow", {"action_id": 2}, (), prepare=with_workflow)
    for enabled in (True, False):
        given: Json = {"enabled": enabled, "trigger_type": "added"}
        await sweep("list_workflows", given, (), prepare=with_workflow)
    await sweep(
        "list_workflows", {"uses_kind": "tag", "uses": "e2e-finance"}, (), prepare=with_workflow
    )
    plain: Json = {
        "type": "apply AI suggestions",
        "ai_suggestion_fields": ["title"],
        "ai_create_missing": False,
        "ai_overwrite_existing": False,
    }
    await sweep("update_workflow", {"workflow": 1, "actions": [plain]}, (), prepare=with_workflow)
    asked: Json = {"name": "AI", "triggers": [{"type": "manual"}], "actions": [plain]}
    await sweep("create_workflow", asked, ())
    part: Json = {"workflow": 1, "operation": "add"}
    for trigger in TRIGGERS:
        await sweep("update_workflow_part", {**part, "trigger": trigger}, (), prepare=with_workflow)
    for action in ACTIONS:
        placed: Json = {**part, "action": action, "position": 1}
        await sweep("update_workflow_part", placed, (), prepare=with_workflow)
    change: Json = {"workflow": 1, "operation": "change"}
    await sweep(
        "update_workflow_part",
        {
            **change,
            "trigger_id": 1,
            "trigger": {"type": "added", "has_tags": [2]},
            "clear": ["match"],
        },
        (),
        prepare=with_workflow,
    )
    await sweep(
        "update_workflow_part",
        {**change, "action_id": 2, "action": {"type": "assignment", "assign_title": "x"}},
        (),
        prepare=with_workflow,
    )
    await sweep(
        "update_workflow_part",
        {"workflow": 1, "operation": "remove", "trigger_id": 1},
        (),
        prepare=with_workflow,
    )
    await sweep("delete_workflow", {"workflow": 1}, (), prepare=with_workflow)
    filters: Json = {"title_contains": "e2e"}
    for chosen in (
        {"document_ids": [1], "exclude_ids": [2], "filters": filters},
        {"document_ids": [1], "saved_view": "e2e-view"},
    ):
        arguments: Json = {"workflow": 2, **chosen, "apply_filters": False, "dry_run": False}
        await sweep("run_workflow", arguments, (), prepare=with_workflow)
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
    for position, (number, spec) in enumerate(SPECS.items()):
        value = SAMPLES[spec.kind]
        shown: Json = {
            "display_mode": ("table", "smallCards", "largeCards")[position % 3],
            "display_fields": ["title", "custom_field_1"],
        }
        rules = [{"rule": number, "value": value}]
        created.append(
            (
                {
                    "name": "New",
                    "rules": rules,
                    "sort_field": "created",
                    "sort_reverse": True,
                    "page_size": 10,
                    **shown,
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
                    **shown,
                },
                None,
            )
        )
        ran.append(({"view": 2, "page": 1, "page_size": 10}, with_view(number, value)))
    # More icons than rule types: every one is sent.
    created.extend(({"name": "New", "icon": icon}, None) for icon in ICONS)
    updated.extend(({"view": 1, "icon": icon}, None) for icon in ICONS)
    await sweep_cases("create_saved_view", created)
    await sweep_cases("update_saved_view", updated)
    await sweep_cases("run_saved_view", ran)
    await sweep("update_saved_view", {"view": 1, "clear_sort": True, "clear_page_size": True}, ())
    cleared: Json = {"view": 1, "clear_display_mode": True, "clear_display_fields": True}
    await sweep("update_saved_view", cleared, ())
    await sweep("list_saved_views", {}, ())
    await sweep("get_saved_view", {"view": 1}, ())
    for pinned in ({"dashboard": True}, {"sidebar": True}, {"dashboard": True, "sidebar": False}):
        await sweep("pin_saved_view", {"view": 1, **pinned}, ())
    await sweep("delete_saved_view", {"view": 1}, ())
    assert not FAILED, "\n".join(FAILED)


TASK = "9f1c0f0a-61d4-4c06-9c5e-6c7a4b1f3e21"


def with_pages(state: FakeState) -> None:
    for document in state.documents:
        document.page_count = 4
    state.email_enabled = True
    state.tasks.append(FakeTask(id=1, task_id=TASK, status="success"))


TRASHED_SHA = hashlib.sha256(b"%PDF-1.4 fake original 2").hexdigest()


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


def with_real_files(state: FakeState) -> None:
    """Files that can be rendered."""
    for document in state.documents:
        document.original = document.archive = pdf("page")


async def test_documents_are_read_with_every_section_and_slice() -> None:
    await sweep("get_instance_overview", {}, ())
    await sweep(
        "get_document",
        {
            "document_id": 1,
            "include": ["notes", "history", "metadata", "versions", "suggestions", "duplicates"],
        },
        (),
    )
    await sweep(
        "get_document_content",
        {"document_id": 1, "offset": 1, "max_chars": 10, "version_id": 1},
        (),
    )
    await sweep("get_document_thumbnail", {"document_id": 1}, ())
    await sweep(
        "get_document_pages",
        {
            "document_id": 1,
            "pages": "1",
            "source": "archive",
            "version_id": 1,
            "size": 256,
            "region": [0, 0, 1, 1],
        },
        (),
        with_real_files,
    )
    await sweep("download_document", {"document_id": 1})
    file = await admin().get_document_file(1, "archive", max_bytes=10_000)
    await sweep(
        "download_document",
        {
            "document_id": 1,
            "kind": "archive",
            "version_id": 1,
            "offset": 1,
            "max_bytes": 5,
            "checksum": hashlib.sha256(file.content).hexdigest(),
        },
        (),
    )
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
    await sweep(
        "correct_document_text",
        {
            "document_id": 1,
            "text_state": SEED_STATE,
            "version_id": 1,
            "passage": "E2E",
            "replacement": "e2e",
            "dry_run": False,
            "offset": 0,
        },
        (),
    )
    await sweep(
        "correct_document_text",
        {"document_id": 1, "text_state": SEED_STATE, "text": "t"},
        (),
    )
    await sweep("delete_document", {"document_id": 2}, ())
    await sweep("list_trash", {"page": 1, "page_size": 10}, ())
    await sweep(
        "list_trash",
        {
            "title": "e2e",
            "document_type": 1,
            "kind": "document",
            "deleted_from": "2020-01-01",
            "deleted_to": "2030-01-01",
            "ids": [2],
        },
        ("kind",),
        prepare=with_trash,
    )
    await sweep(
        "get_trash_entry",
        {"document_id": 2, "text": True, "offset": 1, "max_chars": 5},
        ("text",),
        prepare=with_trash,
    )
    await sweep(
        "get_trashed_file", {"document_id": 2, "file": "original"}, ("file",), prepare=with_trash
    )
    part: Json = {"offset": 1, "max_bytes": 5, "checksum": TRASHED_SHA}
    await sweep("get_trashed_file", {"document_id": 2, **part}, (), prepare=with_trash)
    await sweep("get_task", {"task_id": TASK, "wait_seconds": 0}, (), prepare=with_pages)
    await sweep("ask_paperless_ai", {"question": "q"}, ())
    await sweep("get_ai_suggestions", {"document_id": 1}, ())
    assert not FAILED, "\n".join(FAILED)


async def test_many_documents_are_read_by_ids_filters_and_view() -> None:
    every: Json = {
        "query": "acme",
        "title_contains": "e2e",
        "correspondent": 1,
        "document_type": 1,
        "storage_path": 1,
        "has_correspondent": True,
        "has_document_type": True,
        "has_storage_path": True,
        "has_tags": True,
        "tags_all": [1],
        "tags_any": [1],
        "tags_none": [3],
        "created_from": "2020-01-01",
        "created_to": "2030-01-01",
        "added_from": "2020-01-01",
        "added_to": "2030-01-01",
        "owner": 2,
        "inbox": True,
        "custom_field_query": ["e2e-string", "exact", "hello"],
        "has_duplicates": False,
    }
    paging: Json = {"page": 1, "page_size": 10}
    await sweep("read_documents", {"document_ids": [1, 2], **paging}, ("fields", "ordering"))
    await sweep("read_documents", {"filters": every, **paging}, (), prepare=owned)
    await sweep("read_documents", {"filters": {"more_like_id": 1}, **paging}, ())
    await sweep(
        "read_documents",
        {"document_ids": [1], "saved_view": "e2e-view", "content_chars": 5, "fields": ["content"]},
        (),
    )
    await sweep("read_documents", {"saved_view": "e2e-view", "exclude_ids": [2], **paging}, ())
    assert not FAILED, "\n".join(FAILED)


async def test_the_pdf_tools_take_every_option() -> None:
    # A preview sends nothing, so every case runs for real.
    one = {"document_id": 1, "version_id": 1, "wait_seconds": 0, "dry_run": False}
    await sweep(
        "inspect_pdf",
        {"document_id": 1, "pages": "1", "version_id": 1},
        ("overview", "source"),
        prepare=with_real_files,
    )
    await sweep(
        "insert_pages",
        {
            "document_id": 1,
            "source_document_id": 2,
            "after_page": 1,
            "pages": "1",
            "source_version_id": 2,
            "wait_seconds": 0,
            "dry_run": False,
        },
        ("output",),
        prepare=with_real_files,
    )
    await sweep(
        "rotate_documents",
        {"document_ids": [1], "degrees": 90, "wait_seconds": 0, "dry_run": False},
        ("degrees", "use_original_files"),
        prepare=with_pages,
    )
    await sweep(
        "merge_documents",
        {
            "document_ids": [1, 2],
            "metadata_document_id": 1,
            "delete_originals": True,
            "wait_seconds": 0,
            "dry_run": False,
        },
        ("delete_originals", "archive_fallback", "use_original_files"),
        prepare=with_pages,
    )
    await sweep(
        "split_document",
        {
            "document_id": 1,
            "parts": ["1-2", "3-4"],
            "output": "replace_original",
            "wait_seconds": 0,
            "dry_run": False,
        },
        (),
        prepare=with_pages,
    )
    await sweep(
        "split_document",
        {
            "document_id": 1,
            "parts": ["1-2", "3-4"],
            "output": "new_document",
            "version_id": 1,
            "keep_metadata": False,
        },
        (),
        prepare=with_pages,
    )
    await sweep(
        "split_document",
        {
            "document_id": 1,
            "parts": ["1-2", "3-4"],
            "part_metadata": [
                {"title": "Annex", "document_type": 1, "correspondent": 1},
                {"created": "2026-01-02", "tags": [1]},
            ],
            "wait_seconds": 1,
            "dry_run": False,
        },
        (),
        prepare=with_pages,
    )
    await sweep(
        "extract_pages",
        {**one, "pages": "1", "remove": True, "keep_metadata": True},
        ("remove", "keep_metadata"),
        prepare=with_pages,
    )
    await sweep(
        "extract_pages",
        {"document_id": 1, "pages": "1", "metadata": {"title": "One"}, "wait_seconds": 1},
        (),
        prepare=with_pages,
    )
    await sweep(
        "edit_pdf",
        {**one, "pages": [{"page": 1, "rotate": 90}, {"page": 2}]},
        ("output", "keep_metadata"),
        prepare=with_pages,
    )
    await sweep(
        "delete_pages",
        {**one, "pages": "1", "keep_metadata": True},
        ("output",),
        prepare=with_pages,
    )
    await sweep(
        "remove_document_password",
        {**one, "password": "pw"},
        ("output", "keep_metadata"),
        prepare=with_pages,
    )
    await sweep(
        "reprocess_documents",
        {"document_ids": [1], "remote_ocr": True, "wait_seconds": 0, "dry_run": False},
        ("remote_ocr",),
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
    for tool in ("restore_from_trash", "empty_trash"):
        await shown_first(tool, {"filter": {"kind": "document"}}, prepare=with_trash)
    assert not FAILED, "\n".join(FAILED)


def flags_on(state: FakeState) -> None:
    """Every on/off setting of the configuration is on: switching it off is a change."""
    state.config.update(dict.fromkeys(BOOLEANS, True))


def reader_off(state: FakeState) -> None:
    """The reader is inactive, a staff member and a superuser: each flag is sent the other way."""
    state.users[:] = [
        replace(user, is_active=False, is_staff=True, is_superuser=True)
        if user.username == "e2e-reader"
        else user
        for user in state.users
    ]


def unordered(state: FakeState) -> None:
    """Documents have no owner; a rule runs with the settings the tools do not default to."""
    with_other_mail(state)
    rule = state.mail_rules[0]
    rule.enabled, rule.stop_processing, rule.assign_owner_from_rule = False, True, False
    rule.owner, rule.folder, rule.maximum_age = None, "Elsewhere", 7
    state.mail_accounts[0].imap_security = "none"


def reversed_view(state: FakeState) -> None:
    state.saved_views[0].sort_reverse, state.saved_views[0].sort_field = True, "created"


async def test_values_are_sent_from_the_state_where_they_are_a_change() -> None:
    names = sorted(set(BOOLEANS) & set(CONFIG_SETTINGS))
    await sweep(
        "update_app_config", {"settings": dict.fromkeys(names, False)}, (), prepare=flags_on
    )
    flags = ("is_active", "is_staff", "is_superuser")
    await sweep("update_user", {"user": "e2e-reader", "first_name": "A"}, flags)
    await sweep("update_user", {"user": "e2e-reader", "first_name": "A"}, flags, prepare=reader_off)
    await sweep("create_user", {"username": "sweeper", "password": "S3cret-Passw0rd!x"}, flags)
    await sweep(
        "update_mail_account", {"account_id": 1, "name": "N"}, ("imap_security",), prepare=with_mail
    )
    await sweep(
        "update_mail_account", {"account_id": 1, "name": "N"}, ("imap_security",), prepare=unordered
    )
    booleans = ("enabled", "stop_processing", "assign_owner_from_rule")
    await sweep("update_mail_rule", {"rule_id": 1, "name": "N"}, booleans, prepare=with_mail)
    await sweep("update_mail_rule", {"rule_id": 1, "name": "N"}, booleans, prepare=unordered)
    await sweep(
        "update_mail_rule",
        {"rule_id": 1, "owner": "admin", "folder": "Archive", "maximum_age_days": 31},
        (),
        prepare=unordered,
    )
    await sweep(
        "update_mail_rule",
        {"rule_id": 1, "account_id": 2, "assign_correspondent_from": "none"},
        (),
        prepare=unordered,
    )
    await sweep(
        "update_saved_view", {"view": 1, "name": "N"}, ("sort_reverse",), prepare=reversed_view
    )
    await sweep("update_saved_view", {"view": 1, "name": "N"}, ("sort_reverse",))
    await sweep(
        "create_saved_view",
        {"name": "N", "sort_field": "created", "sort_reverse": True},
        ("sort_reverse",),
    )
    await sweep(
        "bulk_edit_taxonomy",
        {
            "kind": "tag",
            "items": [1],
            "operation": {"op": "set_permissions", "view_users": ["admin"], "merge": True},
        },
        (),
    )
    await sweep(
        "email_documents",
        {"document_ids": [1], "recipients": ["a@b.example"], "subject": "s", "message": "m"},
        ("use_archive_version",),
        prepare=with_pages,
    )
    await sweep(
        "update_taxonomy_item",
        {"kind": "tag", "item": 2, "name": "Z"},
        ("is_inbox_tag", "case_insensitive"),
        prepare=sensitive,
    )
    assert not FAILED, "\n".join(FAILED)


async def test_workflows_take_every_algorithm_flag_schedule_and_action_type() -> None:
    for algorithm in ("none", "any", "all", "exact", "regex", "fuzzy"):
        trigger: Json = {"type": "added", "matching_algorithm": algorithm, "is_insensitive": False}
        if algorithm != "none":
            trigger["match"] = "m"
        await sweep("create_workflow", {"name": "W", "enabled": False, "triggers": [trigger]}, ())
        await sweep("create_workflow", {"name": "W", "enabled": True, "triggers": [trigger]}, ())
        await sweep(
            "update_workflow", {"workflow": 1, "triggers": [trigger]}, (), prepare=with_workflow
        )
    for field in ("modified", "custom_field"):
        schedule = {
            "type": "scheduled",
            "schedule_date_field": field,
            "schedule_date_custom_field": 1 if field == "custom_field" else None,
            "schedule_offset_days": 1,
            "schedule_is_recurring": False,
        }
        await sweep("create_workflow", {"name": "W", "enabled": False, "triggers": [schedule]}, ())
        await sweep(
            "update_workflow", {"workflow": 1, "triggers": [schedule]}, (), prepare=with_workflow
        )
    kept = {
        "remove_tags": [1],
        "remove_correspondents": [1],
        "remove_document_types": [1],
        "remove_storage_paths": [1],
        "remove_custom_fields": [1],
        "remove_owners": ["admin"],
        "remove_all_tags": False,
        "remove_all_correspondents": False,
        "remove_all_document_types": False,
        "remove_all_storage_paths": False,
        "remove_all_custom_fields": False,
        "remove_all_owners": False,
        "remove_all_permissions": False,
        "remove_view_users": ["admin"],
    }
    consumption = {"type": "consumption", "filename": "*.pdf"}
    actions: list[Json] = [
        {"type": "removal", **kept},
        {"type": "move to trash"},
        {"type": "remote OCR"},
        {
            "type": "email",
            "email": {"subject": "s", "body": "b", "to": "a@b.example", "include_document": False},
        },
        {
            "type": "webhook",
            "webhook": {
                "url": "https://hook.example/x",
                "use_params": False,
                "as_json": False,
                "body": "x",
                "include_document": False,
            },
        },
    ]
    for action in actions:
        await sweep(
            "create_workflow",
            {"name": "W", "enabled": False, "triggers": [consumption], "actions": [action]},
            (),
        )
        await sweep(
            "update_workflow",
            {"workflow": 1, "triggers": [consumption], "actions": [action]},
            (),
            prepare=with_workflow,
        )
    await sweep(
        "update_workflow", {"workflow": 1, "name": "N"}, ("enabled",), prepare=with_workflow
    )
    assert not FAILED, "\n".join(FAILED)


async def test_saved_view_rules_of_every_kind_are_run_with_every_value() -> None:
    cases: list[tuple[Json, Prepare | None]] = []
    for number, spec in SPECS.items():
        if spec.kind == "bool":
            cases.append(({"view": 2, "page": 1}, with_view(number, "false")))
    await sweep_cases("run_saved_view", cases)
    assert not FAILED, "\n".join(FAILED)


async def test_the_remaining_tools_have_a_scenario_with_all_their_arguments() -> None:
    link = await step("create_share_link", {"document_id": 1, "file_version": "archive"})
    bundle = await step("create_share_bundle", {"document_ids": [1]})
    _ = await step("delete_share_link", {"link_id": cast("int", link.get("id", 1))})
    _ = await step("rebuild_share_bundle", {"bundle_id": cast("int", bundle.get("id", 1))})
    _ = await step("delete_share_bundle", {"bundle_id": cast("int", bundle.get("id", 1))})
    version = await step(
        "upload_document_version",
        {
            "document_id": 1,
            "content_base64": "JVBERi0xLjQK",
            "filename": "v.pdf",
            "label": "L",
            "wait_seconds": 0,
        },
    )
    _ = await step(
        "delete_document_version",
        {"document_id": 1, "version_id": cast("int", version.get("version_id", 2))},
    )
    _ = await step(
        "bulk_edit_taxonomy",
        {"kind": "tag", "items": [1], "operation": {"op": "delete"}, "dry_run": True},
    )
    await sweep("search_documents", {"custom_field_query": ["e2e-string", "exact", "x"]}, ())
    await sweep("acknowledge_tasks", {"task_ids": [1]}, (), prepare=with_pages)
    await sweep("acknowledge_tasks", {"all_unacknowledged": True}, (), prepare=with_pages)
    await sweep("acknowledge_tasks", {"task_ids": [1], "dry_run": True}, (), prepare=with_pages)
    await sweep(
        "get_task_overview",
        {
            "days": 7,
            "acknowledged": False,
            "task_type": ["mail_fetch", "consume_file"],
            "trigger_source": ["scheduled"],
            "owner": "admin",
            "name": "scan",
            "result": "error",
            "created_from": "2026-11-01T00:00:00Z",
            "created_to": "2026-11-30T00:00:00+01:00",
        },
    )
    await sweep("delete_group", {"group": "e2e-group"}, ())
    await sweep("delete_user", {"user": "e2e-reader"}, ())
    await sweep("get_user", {"user": "e2e-reader"}, ())
    await sweep("get_group", {"group": "e2e-group"}, ())
    await sweep("get_app_config", {}, ())
    await sweep("list_logs", {}, ())
    await sweep("get_log", {"name": "mail", "lines": 10}, (), prepare=with_logs)
    await sweep("get_mail_account", {"account_id": 1}, (), prepare=with_mail)
    await sweep("get_mail_rule", {"rule_id": 1}, (), prepare=with_mail)
    await sweep("delete_mail_account", {"account_id": 2}, (), prepare=with_mail)
    await sweep("delete_mail_rule", {"rule_id": 1}, (), prepare=with_mail)
    await sweep(
        "delete_processed_mail",
        {"mail_ids": [1], "dry_run": False},
        (),
        prepare=with_processed_mail,
    )
    await sweep("list_mail_accounts", {"page": 1, "page_size": 10}, (), prepare=with_mail)
    await sweep(
        "list_mail_rules", {"account_id": 1, "page": 1, "page_size": 10}, (), prepare=with_mail
    )
    await sweep(
        "list_processed_mail",
        {"rule_id": 1, "page": 1, "page_size": 10},
        ("status", "ordering"),
        prepare=with_mail,
    )
    await sweep("ask_paperless_ai", {"question": "q", "document_id": 1}, ())
    await sweep("paperless_api_get", {"path": "/api/tags/", "params": {"page": "1"}}, ())
    assert not FAILED, "\n".join(FAILED)


def with_logs(state: FakeState) -> None:
    state.logs["mail"] = ["one", "two", "three"]
