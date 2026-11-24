"""What an accepted confirmation covers where its text does not tell targets apart (decision-44).

Every case asks, changes the target so that the text stays the same, and asks again: the second
refusal has the same words but another fingerprint, so the first acceptance does not pass it.
All fakes refuse writes: a write would fail the test.
"""

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from fakes.accounts import ADMIN
from fakes.mail import FakeMailAccount, FakeMailRule
from fakes.paperless import (
    FakeDocument,
    FakeGroup,
    FakePaperless,
    FakeState,
    FakeTag,
    FakeTaxonomy,
    FakeUser,
    seeded,
)
from fakes.sharing import FakeShareBundle

from mcp_server_paperless.domain.bulk import (
    AddTags,
    DeleteDocuments,
    DeleteItems,
    ReprocessDocuments,
)
from mcp_server_paperless.domain.documents import DocumentEdit
from mcp_server_paperless.domain.errors import ConfirmationRequired, accepting
from mcp_server_paperless.domain.mail import MailRuleSettings
from mcp_server_paperless.domain.pdf import PageSlot
from mcp_server_paperless.domain.permissions import PermissionsRequest
from mcp_server_paperless.domain.taxonomy import SelectOption
from mcp_server_paperless.domain.taxonomy_edits import TaxonomyEdit
from mcp_server_paperless.domain.views import FilterRule
from mcp_server_paperless.domain.workflows import (
    ActionInput,
    TriggerInput,
    Workflow,
    WorkflowAction,
    WorkflowCreate,
    WorkflowEdit,
    WorkflowTrigger,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.admin_accounts import AccountAdmin
from mcp_server_paperless.services.admin_system import SystemAdmin
from mcp_server_paperless.services.bulk_documents import BulkDocumentEditor
from mcp_server_paperless.services.bulk_taxonomy import BulkTaxonomyEditor
from mcp_server_paperless.services.document_edits import DocumentEditor
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.mail import MailService
from mcp_server_paperless.services.notes import NoteService
from mcp_server_paperless.services.pdf import PdfService
from mcp_server_paperless.services.permissions import PermissionService
from mcp_server_paperless.services.removal import DocumentRemoval
from mcp_server_paperless.services.sharing import SharingService
from mcp_server_paperless.services.tasks import TaskService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.taxonomy_edits import TaxonomyEditor
from mcp_server_paperless.services.trash import TrashService
from mcp_server_paperless.services.versions import VersionService
from mcp_server_paperless.services.views import ViewService
from mcp_server_paperless.services.workflows import WorkflowService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-44")]

WEB = "https://paperless.example"
MANY = range(101, 113)  # enough that every capped list counts the last ones


def fakes(state: FakeState) -> tuple[FakePaperless, TaxonomyResolver]:
    """A fake that refuses writes, and a resolver that reads every time."""
    fake = FakePaperless(state, user=ADMIN[0])
    return fake, TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=0.0)


def trash(state: FakeState) -> Callable[[], Awaitable[object]]:
    state.trash = [FakeDocument(id=n, title="Same") for n in MANY]
    fake, taxonomy = fakes(state)
    service = TrashService(fake, fake, taxonomy, web_url=WEB)
    return lambda: service.empty(None, everything=True)


def swap_trash(state: FakeState) -> None:
    """The last listed entry, which is only counted, is another one now."""
    state.trash[0].id = 1


def bulk_documents(state: FakeState) -> Callable[[], Awaitable[object]]:
    state.documents = [FakeDocument(id=n, title=f"Doc {n}") for n in MANY]
    fake, taxonomy = fakes(state)
    service = BulkDocumentEditor(fake, fake, taxonomy, web_url=WEB)
    operation = AddTags(op="add_tags", tags=["e2e-inbox"])
    return lambda: service.edit(list(MANY), operation, dry_run=False, confirm=False)


def swap_changing(state: FakeState) -> None:
    """Another document already has the tag; the count and the named ones stay."""
    by_id = {d.id: d for d in state.documents}
    by_id[111].tags, by_id[112].tags = by_id[112].tags, by_id[111].tags


def bulk_documents_with_one_done(state: FakeState) -> Callable[[], Awaitable[object]]:
    call = bulk_documents(state)
    state.documents[-1].tags = [4]
    return call


def other_tag(state: FakeState) -> None:
    """The name now belongs to another tag."""
    state.tags = [t for t in state.tags if t.name != "e2e-inbox"]
    state.tags.append(FakeTag(id=44, name="e2e-inbox"))


def bulk_taxonomy(state: FakeState) -> Callable[[], Awaitable[object]]:
    state.tags.extend(FakeTag(id=n, name=f"child {n}", parent=1) for n in MANY)
    fake, taxonomy = fakes(state)
    service = BulkTaxonomyEditor(fake, fake, taxonomy)
    return lambda: service.edit(
        "tag", ["e2e-finance"], DeleteItems(op="delete"), dry_run=False, confirm=False
    )


def swap_child(state: FakeState) -> None:
    state.tags[-1].id = 999


def delete_tag(state: FakeState) -> Callable[[], Awaitable[object]]:
    state.tags.extend(FakeTag(id=n, name=f"child {n}", parent=1) for n in MANY)
    fake, taxonomy = fakes(state)
    service = TaxonomyEditor(fake, fake, taxonomy, fake)
    return lambda: service.delete("tag", "e2e-finance", confirm=False)


def remove_options(state: FakeState) -> Callable[[], Awaitable[object]]:
    options = tuple(SelectOption(id=f"o{n}", label=f"Option {n}") for n in MANY)
    state.custom_fields[-1] = replace(state.custom_fields[-1], select_options=options)
    fake, taxonomy = fakes(state)
    service = TaxonomyEditor(fake, fake, taxonomy, fake)
    edit = TaxonomyEdit(options_remove=tuple(f"Option {n}" for n in MANY[:-1]))
    return lambda: service.update("custom_field", "e2e-select", edit)


def swap_option(state: FakeState) -> None:
    field = state.custom_fields[-1]
    assert field.select_options is not None
    *kept, _, last = field.select_options
    other = SelectOption(id="other", label="Option 111")
    state.custom_fields[-1] = replace(field, select_options=(*kept, other, last))


def permissions(state: FakeState) -> Callable[[], Awaitable[object]]:
    state.documents = [FakeDocument(id=n, title=f"Doc {n}", owner=2) for n in MANY]
    fake, taxonomy = fakes(state)
    service = PermissionService(fake, fake, taxonomy, fake, web_url=WEB)
    request = PermissionsRequest.model_validate({"mode": "replace", "view_users": []})
    for document in state.documents:
        document.viewers = {3}
    return lambda: service.set_permissions("document", [str(n) for n in MANY], request)


def other_viewer(state: FakeState) -> None:
    """One of the documents that are only counted now loses another user's access."""
    state.documents[-1].viewers = {4}


def config(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, _ = fakes(state)
    service = SystemAdmin(fake, fake, max_bytes=10)
    names = ("pages", "image_dpi", "barcode_dpi", "barcode_max_pages", "llm_context_size")
    state.config = {"llm_request_timeout": 1}
    wanted = dict.fromkeys((*names, "llm_request_timeout"), 5)
    return lambda: service.update_config(wanted)  # pyright: ignore[reportArgumentType] -- JSON values


def other_setting(state: FakeState) -> None:
    state.config["llm_request_timeout"] = 2


def account(state: FakeState) -> None:
    state.mail_accounts = [
        FakeMailAccount(
            id=1, name="Mail", imap_server="i.example", imap_port=993, username="u", password="p"
        )
    ]
    state.mail_rules = [FakeMailRule(id=1, name="Rule", account=1, action="delete")]


def mail_fetch(state: FakeState) -> Callable[[], Awaitable[object]]:
    account(state)
    fake, taxonomy = fakes(state)
    service = MailService(fake, fake, taxonomy, fake)
    return lambda: service.process_account(1, confirm=False)


def mail_delete(state: FakeState) -> Callable[[], Awaitable[object]]:
    account(state)
    fake, taxonomy = fakes(state)
    service = MailService(fake, fake, taxonomy, fake)
    return lambda: service.delete_account(1, confirm=False)


def wider_rule(state: FakeState) -> None:
    state.mail_rules[0].filter_from = "everyone"


def other_rule(state: FakeState) -> None:
    state.mail_rules[0].id = 2


def bundle(state: FakeState) -> Callable[[], Awaitable[object]]:
    state.share_bundles = [FakeShareBundle(id=1, documents=(1,), owner=2)]
    fake, _ = fakes(state)
    service = SharingService(fake, fake, fake, web_url=WEB)
    return lambda: service.delete_bundle(1, confirm=False)


def other_bundle(state: FakeState) -> None:
    state.share_bundles[0].documents = (2,)


def workflow(state: FakeState) -> None:
    state.workflows = [
        Workflow(
            id=1,
            name="Filing",
            order=0,
            enabled=False,
            triggers=(WorkflowTrigger(id=1, type=2, settings={"filter_filename": "a"}),),
            actions=(WorkflowAction(id=2, type=5, settings={}),),
        )
    ]


def delete_workflow(state: FakeState) -> Callable[[], Awaitable[object]]:
    workflow(state)
    fake, taxonomy = fakes(state)
    service = WorkflowService(fake, fake, taxonomy)
    return lambda: service.delete(1, confirm=False)


def other_trigger(state: FakeState) -> None:
    trigger = WorkflowTrigger(id=1, type=2, settings={"filter_filename": "b"})
    state.workflows[0] = state.workflows[0].model_copy(update={"triggers": (trigger,)})


def delete_user(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    service = AccountAdmin(fake, fake, taxonomy)
    return lambda: service.delete_user("e2e-reader")


def update_user(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    service = AccountAdmin(fake, fake, taxonomy)
    return lambda: service.update_user("e2e-reader", is_superuser=True)


def create_user(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    service = AccountAdmin(fake, fake, taxonomy)
    return lambda: service.create_user(username="new", groups=["e2e-group"])


def other_user(state: FakeState) -> None:
    """The name now belongs to another user."""
    state.users = [u for u in state.users if u.username != "e2e-reader"]
    state.users.append(FakeUser(id=33, username="e2e-reader"))


def delete_group(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    service = AccountAdmin(fake, fake, taxonomy)
    return lambda: service.delete_group("e2e-group")


def update_group(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    service = AccountAdmin(fake, fake, taxonomy)
    return lambda: service.update_group("e2e-group", permissions=["add_user"])


def other_group(state: FakeState) -> None:
    state.groups = [FakeGroup(id=11, name="e2e-group")]


def other_permissions(state: FakeState) -> None:
    """The group grants more now; `update_group` takes it away without saying it was there."""
    state.groups = [FakeGroup(id=1, name="e2e-group", permissions=frozenset({"add_user"}))]


def correspondents(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    service = BulkTaxonomyEditor(fake, fake, taxonomy)
    return lambda: service.edit(
        "correspondent", ["e2e-acme"], DeleteItems(op="delete"), dry_run=False, confirm=False
    )


def other_correspondent(state: FakeState) -> None:
    state.documents[0].correspondent = 9
    state.correspondents = [FakeTaxonomy(id=9, name="e2e-acme")]


def update_workflow(state: FakeState) -> Callable[[], Awaitable[object]]:
    workflow(state)
    fake, taxonomy = fakes(state)
    service = WorkflowService(fake, fake, taxonomy)
    return lambda: service.update(1, WorkflowEdit(enabled=True), confirm=False)


def create_workflow(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    service = WorkflowService(fake, fake, taxonomy)
    trigger = TriggerInput.model_validate({"type": "document_added", "has_tags": ["e2e-inbox"]})
    action = ActionInput.model_validate({"type": "move_to_trash"})
    spec = WorkflowCreate(name="New", enabled=True, triggers=(trigger,), actions=(action,))
    return lambda: service.create(spec, confirm=False)


def delete_view(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    documents = DocumentService(fake, fake, taxonomy, web_url=WEB)
    service = ViewService(fake, fake, taxonomy, documents)
    return lambda: service.delete("e2e-view", confirm=False)


def other_rules(state: FakeState) -> None:
    state.saved_views[0].filter_rules = (FilterRule(rule_type=6, value="2"),)


def create_rule(state: FakeState) -> Callable[[], Awaitable[object]]:
    account(state)
    fake, taxonomy = fakes(state)
    service = MailService(fake, fake, taxonomy, fake)
    settings = MailRuleSettings(
        name="New", account_id=1, action="delete", assign_tags=("e2e-inbox",)
    )
    return lambda: service.create_rule(settings, confirm=False)


def update_rule(state: FakeState) -> Callable[[], Awaitable[object]]:
    account(state)
    fake, taxonomy = fakes(state)
    service = MailService(fake, fake, taxonomy, fake)
    settings = MailRuleSettings(folder="Other")
    return lambda: service.update_rule(1, settings, clear=(), confirm=False)


def other_layout(state: FakeState) -> None:
    state.mail_rules[0].pdf_layout = "text"


def other_pages(state: FakeState) -> None:
    """A newer file has another page, which the split drops too."""
    state.documents[0].page_count = 9


def split(state: FakeState) -> Callable[[], Awaitable[object]]:
    state.documents = [FakeDocument(id=1, title="Scan", page_count=4)]
    fake, _ = fakes(state)
    service = PdfService(fake, fake, TaskService(fake), web_url=WEB)
    return lambda: service.split(1, ["1", "2"], output="replace_original")


def change_owner(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    documents = DocumentService(fake, fake, taxonomy, web_url=WEB)
    service = DocumentEditor(fake, fake, fake, taxonomy, documents=documents)
    return lambda: service.update(1, DocumentEdit(owner="e2e-reader"))


def other_owner(state: FakeState) -> None:
    state.documents[0].owner = 2


def newer_file(state: FakeState) -> None:
    """Someone uploads a new version of every document."""
    for number, document in enumerate(state.documents, start=900):
        [*_, root] = document.versions
        newer = root.model_copy(update={"id": number, "is_root": False})
        document.versions = [newer, *document.versions]


def scans(state: FakeState) -> FakePaperless:
    """Three PDFs, two of them one page-worth of work for the pdf tools."""
    state.documents = [FakeDocument(id=n, title=f"Scan {n}", page_count=4) for n in (1, 2, 3)]
    return fakes(state)[0]


def pdf_call(
    start: Callable[[PdfService], Awaitable[object]],
) -> Callable[[FakeState], Callable[[], Awaitable[object]]]:
    def make(state: FakeState) -> Callable[[], Awaitable[object]]:
        fake = scans(state)
        service = PdfService(fake, fake, TaskService(fake), web_url=WEB)
        return lambda: start(service)

    return make


def sharing_call(
    start: Callable[[SharingService], Awaitable[object]],
) -> Callable[[FakeState], Callable[[], Awaitable[object]]]:
    def make(state: FakeState) -> Callable[[], Awaitable[object]]:
        fake = scans(state)
        state.email_enabled = True
        service = SharingService(fake, fake, fake, web_url=WEB)
        return lambda: start(service)

    return make


def bulk_call(
    operation: DeleteDocuments | ReprocessDocuments,
) -> Callable[[FakeState], Callable[[], Awaitable[object]]]:
    def make(state: FakeState) -> Callable[[], Awaitable[object]]:
        fake = scans(state)
        service = BulkDocumentEditor(fake, fake, fakes(state)[1], web_url=WEB)
        return lambda: service.edit([1, 2], operation, dry_run=False, confirm=False)

    return make


def delete_document(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake = scans(state)
    service = DocumentRemoval(fake, fake, fakes(state)[1])
    return lambda: service.delete(1)


def merge_versions(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake = scans(state)
    service = VersionService(fake, fake, fakes(state)[1], web_url=WEB)
    return lambda: service.merge(1, [2], None)


def newer_root(state: FakeState) -> None:
    [root] = state.documents[0].versions
    newer = root.model_copy(update={"id": 900, "is_root": False})
    state.documents[0].versions = [newer, root]


def move_account(state: FakeState) -> Callable[[], Awaitable[object]]:
    account(state)
    fake, taxonomy = fakes(state)
    service = MailService(fake, fake, taxonomy, fake)
    return lambda: service.update_account(1, imap_server="other.example")


def other_mailbox(state: FakeState) -> None:
    state.mail_accounts[0].username = "someone else"


def delete_mail_rule(state: FakeState) -> Callable[[], Awaitable[object]]:
    account(state)
    fake, taxonomy = fakes(state)
    service = MailService(fake, fake, taxonomy, fake)
    return lambda: service.delete_rule(1, confirm=False)


def other_email(state: FakeState) -> None:
    state.users = [
        replace(u, email="other@x.org") if u.username == "e2e-reader" else u for u in state.users
    ]


def renamed_note(state: FakeState) -> None:
    """The same length and the same start, another end."""
    document = state.documents[0]
    document.notes = [n.model_copy(update={"note": n.note[:-1] + "!"}) for n in document.notes]


def delete_note(state: FakeState) -> Callable[[], Awaitable[object]]:
    note = state.documents[0].notes[0]
    state.documents[0].notes = [note.model_copy(update={"note": "word " * 60})]
    fake, _ = fakes(state)
    service = NoteService(fake, fake, web_url=WEB)
    return lambda: service.delete(1, 1)


SLOT = [PageSlot(page=1)]


NEWER_FILE = {
    "delete_pages": pdf_call(lambda s: s.delete_pages(1, "2")),
    "edit_pdf": pdf_call(lambda s: s.edit(1, SLOT, output="replace_original")),
    "split": pdf_call(lambda s: s.split(1, ["1", "2"], output="replace_original")),
    "remove_password": pdf_call(lambda s: s.remove_password(1, "x", output="replace_original")),
    "merge": pdf_call(lambda s: s.merge([1, 2], None, delete_originals=True)),
    "reprocess": pdf_call(lambda s: s.reprocess([1, 2])),
    "share link": sharing_call(
        lambda s: s.create_link(1, expires_in_days=1, file_version="archive", confirm=False)
    ),
    "share bundle": sharing_call(
        lambda s: s.create_bundle([1, 2], expires_in_days=1, file_version="archive", confirm=False)
    ),
    "e-mail": sharing_call(
        lambda s: s.email(
            [1],
            recipients=["a@x.org"],
            subject="s",
            message="m",
            use_archive_version=True,
            confirm=False,
        )
    ),
    "bulk delete": bulk_call(DeleteDocuments(op="delete")),
    "bulk reprocess": bulk_call(ReprocessDocuments(op="reprocess")),
}


@pytest.mark.parametrize(
    ("make", "change"),
    [
        (delete_document, newer_file),
        (merge_versions, newer_root),
        (move_account, other_mailbox),
        (mail_fetch, other_mailbox),
        (mail_delete, other_mailbox),
        (create_rule, other_mailbox),
        (update_rule, other_mailbox),
        (delete_mail_rule, wider_rule),
        (update_user, other_email),
        (delete_user, other_email),
        (delete_group, other_permissions),
        (delete_note, renamed_note),
        (update_workflow, other_trigger),
        (create_workflow, other_tag),
        (delete_view, other_rules),
        (create_rule, other_tag),
        (update_rule, other_layout),
        (split, other_pages),
        (change_owner, other_owner),
        (trash, swap_trash),
        (bulk_documents_with_one_done, swap_changing),
        (bulk_documents, other_tag),
        (bulk_taxonomy, swap_child),
        (correspondents, other_correspondent),
        (delete_tag, swap_child),
        (remove_options, swap_option),
        (permissions, other_viewer),
        (config, other_setting),
        (mail_fetch, wider_rule),
        (mail_delete, other_rule),
        (bundle, other_bundle),
        (delete_workflow, other_trigger),
        (delete_user, other_user),
        (update_user, other_user),
        (create_user, other_group),
        (delete_group, other_group),
        (update_group, other_group),
    ],
)
async def test_an_acceptance_does_not_cover_other_targets_behind_the_same_text(
    make: Callable[[FakeState], Callable[[], Awaitable[object]]],
    change: Callable[[FakeState], None],
) -> None:
    state = seeded()
    await refused_twice(state, make(state), change)


@pytest.mark.parametrize("name", NEWER_FILE)
async def test_an_acceptance_does_not_cover_a_file_added_meanwhile(name: str) -> None:
    state = seeded()
    await refused_twice(state, NEWER_FILE[name](state), newer_file)


async def refused_twice(
    state: FakeState, call: Callable[[], Awaitable[object]], change: Callable[[FakeState], None]
) -> None:
    with pytest.raises(ConfirmationRequired) as first:
        _ = await call()
    with pytest.raises(ConfirmationRequired) as again:
        _ = await call()
    assert again.value.fingerprint == first.value.fingerprint

    change(state)
    with accepting(first.value.fingerprint), pytest.raises(ConfirmationRequired) as second:
        _ = await call()

    assert second.value.message == first.value.message
    assert second.value.fingerprint != first.value.fingerprint
