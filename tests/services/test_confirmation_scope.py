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

from mcp_server_paperless.domain.bulk import AddTags, DeleteItems
from mcp_server_paperless.domain.errors import ConfirmationRequired, accepting
from mcp_server_paperless.domain.permissions import PermissionsRequest
from mcp_server_paperless.domain.taxonomy import SelectOption
from mcp_server_paperless.domain.taxonomy_edits import TaxonomyEdit
from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.admin_accounts import AccountAdmin
from mcp_server_paperless.services.admin_system import SystemAdmin
from mcp_server_paperless.services.bulk_documents import BulkDocumentEditor
from mcp_server_paperless.services.bulk_taxonomy import BulkTaxonomyEditor
from mcp_server_paperless.services.mail import MailService
from mcp_server_paperless.services.permissions import PermissionService
from mcp_server_paperless.services.sharing import SharingService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.taxonomy_edits import TaxonomyEditor
from mcp_server_paperless.services.trash import TrashService
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


def correspondents(state: FakeState) -> Callable[[], Awaitable[object]]:
    fake, taxonomy = fakes(state)
    service = BulkTaxonomyEditor(fake, fake, taxonomy)
    return lambda: service.edit(
        "correspondent", ["e2e-acme"], DeleteItems(op="delete"), dry_run=False, confirm=False
    )


def other_correspondent(state: FakeState) -> None:
    state.documents[0].correspondent = 9
    state.correspondents = [FakeTaxonomy(id=9, name="e2e-acme")]


@pytest.mark.parametrize(
    ("make", "change"),
    [
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
    call = make(state)
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
