"""Building blocks for tests of the confirmation dialog: answers, and a call of every tool.

`CALLS` holds, per tool with a `confirm` parameter, arguments that need the confirmation on the
state `world()` makes.
"""

import base64
from typing import TYPE_CHECKING, Final

from fakes.mail import FakeMailAccount, FakeMailRule
from fakes.paperless import FakeDocument, FakeFile, FakePaperless, FakeState, seeded
from fakes.sharing import FakeShareBundle, FakeShareLink
from fastmcp.client.elicitation import ElicitResult

from mcp_server_paperless.domain.documents import DocumentVersion
from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger
from mcp_server_paperless.services.documents import text_state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

type Json = dict[str, object]
type Handler = Callable[[str, object, object, object], Awaitable[ElicitResult[object]]]

NOTE: Final[Json] = {"document_id": 1, "note_id": 1}
YES: Final[dict[str, bool]] = {"confirm": True}
NOT_AGAIN: Final = "Do not call the tool again on your own; ask the user what they want."
KNOWN: Final = b"%PDF-1.4 a file Paperless holds"
PASSWORD: Final = "S3cret-Passw0rd!x"


def world() -> FakeState:
    """The seeded state with one object of every kind a destructive tool needs."""
    state = seeded()
    state.email_enabled = True
    state.documents.extend(
        [
            FakeDocument(id=10, title="Scan one", owner=2, page_count=4),
            FakeDocument(id=11, title="Scan two", owner=2, page_count=2),
            FakeDocument(id=12, title="Locked", owner=2, page_count=None, password=PASSWORD),
        ]
    )
    [root] = state.documents[0].versions
    newer = DocumentVersion(
        id=20, added=root.added, version_label=None, is_root=False, checksum="20"
    )
    state.documents[0].versions.insert(0, newer)
    for version in (root, newer):
        state.documents[0].version_files[version.id] = FakeFile(
            b"%PDF-1.4", None, "text", "application/pdf"
        )
    state.trash = [FakeDocument(id=30, title="Old")]
    state.documents.append(FakeDocument(id=13, title="Known", owner=2, original=KNOWN))
    state.documents.append(FakeDocument(id=14, title="Versions", owner=2))
    state.documents.append(FakeDocument(id=15, title="Known copy", owner=2, original=KNOWN))
    state.share_links = [FakeShareLink(id=1, document=1, owner=2)]
    state.share_bundles = [FakeShareBundle(id=1, documents=(1,), owner=2)]
    state.mail_accounts = [
        FakeMailAccount(
            id=1,
            name="Mail",
            imap_server="imap.example.com",
            imap_port=993,
            username="me",
            password=PASSWORD,
            owner=2,
        )
    ]
    state.mail_rules = [FakeMailRule(id=1, name="Rule", account=1, owner=2)]
    state.workflows = [
        Workflow(
            id=1,
            name="Filing",
            order=0,
            enabled=False,
            triggers=(WorkflowTrigger(id=1, type=2, settings={"filter_has_tags": [1]}),),
            actions=(WorkflowAction(id=2, type=5, settings={}),),
        ),
        Workflow(
            id=2,
            name="By hand",
            order=1,
            enabled=True,
            triggers=(WorkflowTrigger(id=3, type=5, settings={"filter_has_tags": [1]}),),
            actions=(WorkflowAction(id=4, type=1, settings={"assign_tags": [4]}),),
        ),
    ]
    state.workflow_object_id[0] = 4
    return state


CALLS: Final[dict[str, Json]] = {
    "update_taxonomy_item": {
        "kind": "custom_field",
        "item": "e2e-select",
        "select_options_remove": ["beta"],
    },
    "delete_taxonomy_item": {"kind": "tag", "item": "e2e-finance"},
    "update_document": {"document_id": 10, "owner": "e2e-reader"},
    "delete_note": NOTE,
    "delete_document": {"document_id": 10},
    "bulk_edit_documents": {
        "document_ids": [10, 11],
        "operation": {"op": "add_tags", "tags": ["e2e-inbox"]},
        "dry_run": False,
    },
    "update_documents": {
        "documents": [
            {"document_id": 10, "title": "Renamed", "owner": "e2e-reader"},
            {"document_id": 11, "tags_add": ["e2e-inbox"]},
        ],
    },
    "add_notes": {"document_ids": [10, 11], "note": "Paid"},
    "bulk_edit_taxonomy": {
        "kind": "correspondent",
        "items": ["e2e-acme"],
        "operation": {"op": "delete"},
        "dry_run": False,
    },
    "delete_document_version": {"document_id": 1, "version_id": 20},
    "merge_documents_as_versions": {"root_document_id": 10, "source_document_ids": [11]},
    "correct_document_text": {
        "document_id": 10,
        "text_state": text_state(10, 10, ""),
        "text": "typed from the page",
    },
    "resolve_duplicates": {
        "keep_document_id": 13,
        "duplicate_ids": [15],
        "action": "merge_as_versions",
    },
    "delete_saved_view": {"view": "e2e-view"},
    "empty_trash": {"all": True},
    "rotate_documents": {"document_ids": [1], "degrees": 90, "use_original_files": True},
    "merge_documents": {"document_ids": [10, 11], "delete_originals": True},
    "split_document": {"document_id": 10, "parts": ["1", "2"], "output": "replace_original"},
    "extract_pages": {"document_id": 10, "pages": "1", "remove": True},
    "delete_pages": {"document_id": 10, "pages": "2"},
    "edit_pdf": {"document_id": 10, "pages": [{"page": 1}], "output": "replace_original"},
    "remove_document_password": {
        "document_id": 12,
        "password": PASSWORD,
        "output": "replace_original",
    },
    "reprocess_documents": {"document_ids": [10]},
    "create_share_link": {"document_id": 1, "expires_in_days": 7},
    "delete_share_link": {"link_id": 1},
    "create_share_bundle": {"document_ids": [1, 10], "expires_in_days": 7},
    "delete_share_bundle": {"bundle_id": 1},
    "email_documents": {
        "document_ids": [1],
        "recipients": ["a@example.com"],
        "subject": "Invoice",
        "message": "Attached.",
    },
    "set_permissions": {"kind": "document", "items": ["10"], "view_users": ["e2e-reader"]},
    "update_mail_account": {"account_id": 1, "imap_server": "other.example.com"},
    "delete_mail_account": {"account_id": 1},
    "process_mail_account": {"account_id": 1},
    "create_mail_rule": {"name": "New", "account_id": 1, "action": "delete"},
    "update_mail_rule": {"rule_id": 1, "action": "delete"},
    "delete_mail_rule": {"rule_id": 1},
    "create_user": {"username": "new", "password": PASSWORD, "groups": ["e2e-group"]},
    "update_user": {"user": "e2e-reader", "is_superuser": True},
    "delete_user": {"user": "e2e-reader"},
    "create_group": {"name": "new", "permissions": ["add_user"]},
    "update_group": {"group": "e2e-group", "permissions": ["add_user"]},
    "delete_group": {"group": "e2e-group"},
    "update_app_config": {"settings": {"app_title": "Vault"}, "llm_api_key": PASSWORD},
    "run_task": {"task_type": "train_classifier"},
    "create_workflow": {
        "name": "New",
        "enabled": True,
        "triggers": [{"type": "document_added"}],
        "actions": [{"type": "move_to_trash"}],
    },
    "update_workflow": {"workflow": "Filing", "enabled": True},
    "delete_workflow": {"workflow": "Filing"},
    "run_workflow": {"workflow": "By hand", "document_ids": [10, 11], "apply_filters": False},
    "upload_document": {
        "content_base64": base64.b64encode(KNOWN).decode(),
        "filename": "x.pdf",
    },
    "upload_document_version": {
        "document_id": 14,
        "content_base64": base64.b64encode(KNOWN).decode(),
        "filename": "x.pdf",
    },
}


def answering(
    action: str,
    content: object = None,
    *,
    seen: list[str] | None = None,
    before: Callable[[], Awaitable[None]] | None = None,
) -> Handler:
    """A client that answers every dialog like this; `before` runs while it is open."""

    async def handler(
        message: str, _type: object, _params: object, _context: object
    ) -> ElicitResult[object]:
        if seen is not None:
            seen.append(message)
        if before is not None:
            await before()
        return ElicitResult[object](action=action, content=content)  # pyright: ignore[reportArgumentType] -- tests send wrong ones

    return handler


def source(state: FakeState, *, writes: bool = True) -> FakePaperless:
    """The admin's fake on `state`; one without writes fails the test on any write."""
    return FakePaperless(state, user="admin", allow_writes=writes)
