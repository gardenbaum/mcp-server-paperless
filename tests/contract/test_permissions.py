"""Contract of PermissionReader and PermissionWriter: the same assertions on the fake and live.

Facts probed on Paperless 3.4.2 (PLMCP-28):

- A PATCH with ``set_permissions`` replaces, per action and per kind, the lists it carries and
  keeps the ones it leaves out; the port always sends all four, so the result is what was sent.
  Granting change also grants view. An unknown user or group is a 400 for the whole request.
- ``full_perms=true`` shows the grants of the objects the user may see; an object the user may
  not see is not listed.
- Listing users needs ``view_user`` and groups ``view_group``; the rows of users carry names,
  flags and group ids.
"""

import uuid
from typing import TYPE_CHECKING

import pytest
from fakes.accounts import ADMIN, BLIND, READER
from pydantic import SecretStr

from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.domain.mail import MailAccountValues, MailRuleValues
from mcp_server_paperless.domain.permissions import AccessChange
from mcp_server_paperless.domain.taxonomy import TaxonomyValues

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from contract.conftest import Backend, Ports
    from mcp_server_paperless.domain.bulk import BulkKind
    from mcp_server_paperless.domain.permissions import PermissionKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-12")]

GET = "PermissionReader.get_permissions"
USERS = "PermissionReader.list_users"
GROUPS = "PermissionReader.list_groups"
SET = "PermissionWriter.set_permissions"
MISSING = 999_999
TAXONOMY: tuple[BulkKind, ...] = ("tag", "correspondent", "document_type", "storage_path")
CHANGER = frozenset({"view_document", "change_document", "view_tag", "change_tag"})


async def make(ports: Ports, kind: BulkKind) -> int:
    """A new object of its own, named for cleanup (`contract-tag-`, `contract-item-`)."""
    required = {"storage_path": {"path": "contract/{{ title }}"}}.get(kind, {})
    prefix = "contract-tag-" if kind == "tag" else "contract-item-"
    values = TaxonomyValues.model_validate(required)
    created = await ports.taxonomy_writes.create_taxonomy_item(
        kind, f"{prefix}{uuid.uuid4().hex}", values
    )
    return created.id


async def user_id(ports: Ports, username: str) -> int:
    page = await ports.permissions.list_users(name_contains=username, page=1, page_size=100)
    [found] = [row for row in page.rows if row.username == username]
    return found.id


async def group_id(ports: Ports) -> int:
    page = await ports.permissions.list_groups(name_contains="e2e-group", page=1, page_size=100)
    [found] = [row for row in page.rows if row.name == "e2e-group"]
    return found.id


# --- get_permissions


@pytest.mark.contract(GET)
async def test_the_owner_and_grants_are_read_in_the_order_asked_and_hidden_objects_are_left_out(
    backend: Backend,
) -> None:
    ports = backend.admin
    first, second = backend.new_documents(2)
    reader = await user_id(ports, READER[0])
    admin = await user_id(ports, ADMIN[0])
    group = await group_id(ports)
    grants = ObjectPermissions(
        view_users=(reader, admin), view_groups=(group,), change_users=(admin,)
    )
    _ = await ports.permission_writes.set_permissions(
        "document", second, AccessChange(permissions=grants)
    )

    found = await ports.permissions.get_permissions("document", [second, MISSING, first])

    assert [row.id for row in found] == [second, first]
    assert found[0].owner == admin
    assert found[0].permissions == ObjectPermissions(
        view_users=tuple(sorted((reader, admin))),
        view_groups=(group,),
        change_users=(admin,),
    )
    assert found[1].permissions == ObjectPermissions()
    title = (await ports.documents.get_document(first)).title
    assert found[1].name == title
    assert await ports.permissions.get_permissions("document", []) == []
    # The reader sees only the document shared with it, and the grants of that one.
    seen = await backend.reader.permissions.get_permissions("document", [first, second])
    assert [row.id for row in seen] == [second]
    assert seen[0].permissions == found[0].permissions


@pytest.mark.contract(GET)
@pytest.mark.parametrize("kind", TAXONOMY)
async def test_the_grants_of_taxonomy_objects_are_read(backend: Backend, kind: BulkKind) -> None:
    ports = backend.admin
    item = await make(ports, kind)
    reader = await user_id(ports, READER[0])
    grants = ObjectPermissions(view_users=(reader,), change_users=(), view_groups=())
    _ = await ports.permission_writes.set_permissions(kind, item, AccessChange(permissions=grants))

    [found] = await ports.permissions.get_permissions(kind, [item])

    assert found.permissions == grants == backend.grants(kind, item).permissions
    assert found.owner == backend.grants(kind, item).owner


@pytest.mark.contract(GET)
async def test_reading_grants_needs_the_permission_to_view_the_kind(backend: Backend) -> None:
    [document] = backend.new_documents(1)

    with pytest.raises(PermissionDenied):
        _ = await backend.blind.permissions.get_permissions("document", [document])
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.permissions.get_permissions("tag", [1])


# --- set_permissions


@pytest.mark.contract(SET)
async def test_the_lists_given_replace_the_lists_and_a_change_grant_brings_a_view_grant(
    backend: Backend,
) -> None:
    ports = backend.admin
    [document] = backend.new_documents(1)
    reader, blind = await user_id(ports, READER[0]), await user_id(ports, BLIND[0])
    group = await group_id(ports)

    first = await ports.permission_writes.set_permissions(
        "document",
        document,
        AccessChange(
            permissions=ObjectPermissions(
                view_users=(reader, blind), view_groups=(group,), change_users=(blind,)
            )
        ),
    )
    second = await ports.permission_writes.set_permissions(
        "document",
        document,
        AccessChange(
            permissions=ObjectPermissions(
                view_users=(blind,),
                change_users=(blind,),
                change_groups=(group,),
                view_groups=(group,),
            )
        ),
    )
    emptied = await ports.permission_writes.set_permissions(
        "document", document, AccessChange(permissions=ObjectPermissions())
    )

    assert first.permissions == ObjectPermissions(
        view_users=tuple(sorted((reader, blind))), view_groups=(group,), change_users=(blind,)
    )
    assert second.permissions == ObjectPermissions(
        view_users=(blind,), view_groups=(group,), change_users=(blind,), change_groups=(group,)
    )
    assert emptied.permissions == ObjectPermissions()
    assert (await ports.permissions.get_permissions("document", [document]))[0] == emptied


@pytest.mark.contract(SET)
@pytest.mark.parametrize("kind", TAXONOMY)
async def test_the_owner_is_set_and_cleared_only_when_the_change_says_so(
    backend: Backend, kind: BulkKind
) -> None:
    ports = backend.admin
    item = await make(ports, kind)
    reader = await user_id(ports, READER[0])
    admin = await user_id(ports, ADMIN[0])
    grants = ObjectPermissions(view_users=(reader,))

    handed = await ports.permission_writes.set_permissions(
        kind, item, AccessChange(owner=reader, permissions=grants)
    )
    cleared = await ports.permission_writes.set_permissions(kind, item, AccessChange(owner=None))
    untouched = await ports.permission_writes.set_permissions(
        kind, item, AccessChange(permissions=ObjectPermissions(view_users=(admin,)))
    )

    assert (handed.owner, handed.permissions) == (reader, grants)
    assert cleared.owner is None
    assert cleared.permissions == grants  # an owner-only change keeps the grants
    assert untouched.owner is None  # a grants-only change keeps the owner
    assert backend.grants(kind, item).owner is None
    assert backend.grants(kind, item).permissions == ObjectPermissions(view_users=(admin,))


@pytest.mark.contract(SET)
async def test_an_unknown_user_or_group_or_owner_is_refused_and_changes_nothing(
    backend: Backend,
) -> None:
    ports = backend.admin
    [document] = backend.new_documents(1)
    reader = await user_id(ports, READER[0])
    before = (await ports.permissions.get_permissions("document", [document]))[0]

    for change in (
        AccessChange(permissions=ObjectPermissions(view_users=(reader, MISSING))),
        AccessChange(permissions=ObjectPermissions(view_groups=(MISSING,))),
        AccessChange(owner=MISSING, permissions=ObjectPermissions(view_users=(reader,))),
    ):
        with pytest.raises(Validation):
            _ = await ports.permission_writes.set_permissions("document", document, change)

    assert (await ports.permissions.get_permissions("document", [document]))[0] == before


@pytest.mark.contract(SET)
async def test_an_object_that_does_not_exist_or_is_hidden_is_not_found(backend: Backend) -> None:
    [document] = backend.new_documents(1)
    reader = await user_id(backend.admin, READER[0])
    change = AccessChange(permissions=ObjectPermissions(view_users=(reader,)))
    restricted = backend.restricted(CHANGER)

    with pytest.raises(NotFound):
        _ = await backend.admin.permission_writes.set_permissions("document", MISSING, change)
    with pytest.raises(NotFound):
        _ = await restricted.permission_writes.set_permissions("document", document, change)


@pytest.mark.contract(SET)
async def test_only_the_owner_or_a_superuser_with_the_permission_may_change_permissions(
    backend: Backend,
) -> None:
    # One document each: sharing a document replaces the grants an earlier user got.
    [shared, viewed] = backend.new_documents(2)
    reader = await user_id(backend.admin, READER[0])
    change = AccessChange(permissions=ObjectPermissions(view_users=(reader,)))
    # A change grant is not enough: the document is the admin's.
    sharer = backend.restricted(CHANGER, change=[shared])
    # The viewer lacks the global change permission.
    viewer = backend.restricted(frozenset({"view_document"}), view=[viewed])

    with pytest.raises(PermissionDenied):
        _ = await sharer.permission_writes.set_permissions("document", shared, change)
    with pytest.raises(PermissionDenied):
        _ = await viewer.permission_writes.set_permissions("document", viewed, change)

    for document in (shared, viewed):
        [after] = await backend.admin.permissions.get_permissions("document", [document])
        assert reader not in after.permissions.view_users


@pytest.mark.contract(SET)
@pytest.mark.parametrize("kind", TAXONOMY)
async def test_an_owner_change_that_makes_a_name_clash_is_refused_and_changes_nothing(
    backend: Backend, kind: BulkKind
) -> None:
    ports = backend.admin
    reader = await user_id(ports, READER[0])
    admin = await user_id(ports, ADMIN[0])
    name = f"contract-item-{uuid.uuid4().hex}"
    required = {"storage_path": {"path": "contract/{{ title }}"}}.get(kind, {})
    values = TaxonomyValues.model_validate(required)
    first = (await ports.taxonomy_writes.create_taxonomy_item(kind, name, values)).id
    _ = await ports.permission_writes.set_permissions(kind, first, AccessChange(owner=reader))
    second = (await ports.taxonomy_writes.create_taxonomy_item(kind, name, values)).id

    with pytest.raises(Validation):
        _ = await ports.permission_writes.set_permissions(kind, second, AccessChange(owner=reader))

    assert backend.grants(kind, second).owner == admin


@pytest.mark.contract(SET)
async def test_unknown_users_are_refused_before_ownership_is_checked(backend: Backend) -> None:
    [document] = backend.new_documents(1)
    sharer = backend.restricted(CHANGER, change=[document])  # does not own the document

    with pytest.raises(Validation):
        _ = await sharer.permission_writes.set_permissions(
            "document", document, AccessChange(permissions=ObjectPermissions(view_users=(MISSING,)))
        )


# --- list_users and list_groups


@pytest.mark.contract(USERS)
async def test_users_are_listed_compactly_by_username_and_can_be_filtered_and_paged(
    backend: Backend,
) -> None:
    ports = backend.admin

    everyone = await ports.permissions.list_users(name_contains="e2e-", page=1, page_size=100)
    first = await ports.permissions.list_users(name_contains="e2e-", page=1, page_size=1)
    second = await ports.permissions.list_users(name_contains="E2E-", page=2, page_size=1)
    admin = await ports.permissions.list_users(name_contains="admin", page=1, page_size=100)

    usernames = [row.username for row in everyone.rows]
    assert {READER[0], BLIND[0]} <= set(usernames)
    assert usernames == sorted(usernames)
    assert (len(first.rows), first.next_page) == (1, 2)
    assert first.count == second.count == everyone.count
    assert second.rows[0].username == usernames[1]
    [root] = [row for row in admin.rows if row.username == ADMIN[0]]
    assert (root.is_superuser, root.is_active) == (True, True)
    [reader] = [row for row in everyone.rows if row.username == READER[0]]
    assert (reader.is_superuser, reader.is_active, reader.group_ids) == (False, True, ())
    with pytest.raises(NotFound):
        _ = await ports.permissions.list_users(
            name_contains="e2e-", page=everyone.count + 1, page_size=1
        )


@pytest.mark.contract(USERS)
async def test_users_are_ordered_by_username_ignoring_case(backend: Backend) -> None:
    prefix = f"cu{uuid.uuid4().hex[:8]}-"
    _ = backend.restricted(frozenset({"view_user"}), name=f"{prefix}Bravo")
    _ = backend.restricted(frozenset({"view_user"}), name=f"{prefix}alpha")
    _ = backend.restricted(frozenset({"view_user"}), name=f"{prefix}Charlie")

    page = await backend.admin.permissions.list_users(
        name_contains=prefix.upper(), page=1, page_size=100
    )

    assert [row.username for row in page.rows] == [
        f"{prefix}alpha",
        f"{prefix}Bravo",
        f"{prefix}Charlie",
    ]


@pytest.mark.contract(USERS)
async def test_listing_users_needs_the_permission_to_view_them(backend: Backend) -> None:
    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.permissions.list_users(name_contains=None, page=1, page_size=25)
    viewer = backend.restricted(frozenset({"view_user"}))

    page = await viewer.permissions.list_users(name_contains=READER[0], page=1, page_size=25)

    assert [row.username for row in page.rows] == [READER[0]]


@pytest.mark.contract(GROUPS)
async def test_groups_are_listed_by_name_and_can_be_filtered(backend: Backend) -> None:
    ports = backend.admin

    found = await ports.permissions.list_groups(name_contains="E2E-GR", page=1, page_size=25)
    none = await ports.permissions.list_groups(name_contains="no-such-group", page=1, page_size=25)

    assert "e2e-group" in [row.name for row in found.rows]
    assert (none.count, none.rows, none.next_page) == (0, (), None)
    with pytest.raises(NotFound):
        _ = await ports.permissions.list_groups(name_contains="e2e-group", page=99, page_size=1)


@pytest.mark.contract(GROUPS)
async def test_listing_groups_needs_the_permission_to_view_them(backend: Backend) -> None:
    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.permissions.list_groups(name_contains=None, page=1, page_size=25)
    viewer = backend.restricted(frozenset({"view_group"}))

    page = await viewer.permissions.list_groups(name_contains="e2e-group", page=1, page_size=25)

    assert [row.name for row in page.rows] == ["e2e-group"]


# --- mail accounts and rules (PLMCP-81.5)


@pytest.fixture
async def account(backend: Backend) -> AsyncIterator[int]:
    """A mail account of the admin; its rules go with it."""
    writes = backend.admin.mail_writes
    made = await writes.create_mail_account(
        MailAccountValues(
            name=f"contract-{uuid.uuid4().hex[:12]}",
            imap_server="127.0.0.1",
            imap_port=1,
            username="u",
            password=SecretStr("contract-Passw0rd"),
        )
    )
    yield made.id
    await writes.delete_mail_account(made.id)


async def mail_object(backend: Backend, kind: PermissionKind, account: int) -> int:
    if kind == "mail_account":
        return account
    values = MailRuleValues(name=f"contract-{uuid.uuid4().hex[:12]}", account_id=account)
    return (await backend.admin.mail_writes.create_mail_rule(values)).id


MAIL: tuple[PermissionKind, ...] = ("mail_account", "mail_rule")
MAILING = frozenset(
    {
        *("view_mailaccount", "change_mailaccount", "add_mailaccount"),
        *("view_mailrule", "change_mailrule", "add_mailrule"),
    }
)


@pytest.mark.decision("decision-87")
@pytest.mark.contract(GET)
@pytest.mark.contract(SET)
@pytest.mark.parametrize("kind", MAIL)
async def test_mail_objects_take_owner_and_grants_and_show_them(
    backend: Backend, account: int, kind: PermissionKind
) -> None:
    ports = backend.admin
    number = await mail_object(backend, kind, account)
    reader, admin = await user_id(ports, READER[0]), await user_id(ports, ADMIN[0])
    group = await group_id(ports)
    grants = ObjectPermissions(
        view_users=(reader,), view_groups=(group,), change_users=(reader,), change_groups=()
    )

    changed = await ports.permission_writes.set_permissions(
        kind, number, AccessChange(permissions=grants)
    )
    [read] = await ports.permissions.get_permissions(kind, [number, MISSING])

    assert changed == read
    assert (read.id, read.owner, read.permissions) == (number, admin, grants)
    handed = await ports.permission_writes.set_permissions(kind, number, AccessChange(owner=reader))
    assert (handed.owner, handed.permissions) == (reader, grants)
    cleared = await ports.permission_writes.set_permissions(
        kind, number, AccessChange(owner=None, permissions=ObjectPermissions())
    )
    assert (cleared.owner, cleared.permissions) == (None, ObjectPermissions())


@pytest.mark.decision("decision-87")
@pytest.mark.contract(SET)
@pytest.mark.parametrize("kind", MAIL)
async def test_a_change_grant_on_a_mail_object_does_not_let_the_grantee_grant(
    backend: Backend, account: int, kind: PermissionKind
) -> None:
    name = f"contract-user-{uuid.uuid4().hex[:12]}"
    user = backend.restricted(MAILING, name=name)
    number = await mail_object(backend, kind, account)
    grantee = await user_id(backend.admin, name)

    with pytest.raises(NotFound):
        _ = await user.permission_writes.set_permissions(kind, number, AccessChange(owner=grantee))
    assert await user.permissions.get_permissions(kind, [number]) == []
    _ = await backend.admin.permission_writes.set_permissions(
        kind, number, AccessChange(permissions=ObjectPermissions(change_users=(grantee,)))
    )
    [seen] = await user.permissions.get_permissions(kind, [number])

    assert seen.permissions.change_users == (grantee,)
    for change in (
        AccessChange(owner=grantee),
        AccessChange(permissions=ObjectPermissions()),
    ):
        with pytest.raises(PermissionDenied):
            _ = await user.permission_writes.set_permissions(kind, number, change)


@pytest.mark.decision("decision-87")
@pytest.mark.contract(SET)
async def test_what_a_change_grant_on_an_account_gives(backend: Backend, account: int) -> None:
    """The note of the confirmation: another server, rules of one's own, nothing on other rules."""
    name = f"contract-user-{uuid.uuid4().hex[:12]}"
    user = backend.restricted(MAILING, name=name)
    rule = await mail_object(backend, "mail_rule", account)
    grantee = await user_id(backend.admin, name)
    _ = await backend.admin.permission_writes.set_permissions(
        "mail_account",
        account,
        AccessChange(permissions=ObjectPermissions(change_users=(grantee,))),
    )

    moved = await user.mail_writes.update_mail_account(
        account, MailAccountValues(imap_server="127.0.0.2")
    )
    own = await user.mail_writes.create_mail_rule(
        MailRuleValues(
            name=f"contract-{uuid.uuid4().hex[:12]}", account_id=account, action="delete"
        )
    )

    assert (moved.imap_server, moved.can_change) == ("127.0.0.2", True)
    assert own.owner == grantee
    assert rule not in {row.id for row in await user.mail.list_mail_rules()}
    with pytest.raises(NotFound):
        _ = await user.mail.get_mail_rule(rule)
    with pytest.raises(PermissionDenied):  # only its owner can fetch at once
        await user.mail_writes.process_mail_account(account)


@pytest.mark.decision("decision-87")
@pytest.mark.contract(GET)
@pytest.mark.contract(SET)
@pytest.mark.parametrize("kind", MAIL)
async def test_mail_grants_need_the_permissions_of_the_kind_and_known_users(
    backend: Backend, account: int, kind: PermissionKind
) -> None:
    number = await mail_object(backend, kind, account)
    change = AccessChange(permissions=ObjectPermissions(view_users=(MISSING,)))

    with pytest.raises(PermissionDenied):
        _ = await backend.blind.permissions.get_permissions(kind, [number])
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.permission_writes.set_permissions(kind, number, change)
    with pytest.raises(Validation):
        _ = await backend.admin.permission_writes.set_permissions(kind, number, change)
    with pytest.raises(NotFound):
        _ = await backend.admin.permission_writes.set_permissions(
            kind, MISSING, AccessChange(owner=None)
        )


@pytest.mark.decision("decision-87")
@pytest.mark.contract(SET)
@pytest.mark.parametrize("kind", MAIL)
async def test_an_owner_who_is_no_superuser_and_anyone_on_an_unowned_object_may_grant(
    backend: Backend, account: int, kind: PermissionKind
) -> None:
    name = f"contract-user-{uuid.uuid4().hex[:12]}"
    user = backend.restricted(MAILING, name=name)
    number = await mail_object(backend, kind, account)
    owner, reader = await user_id(backend.admin, name), await user_id(backend.admin, READER[0])
    grants = ObjectPermissions(view_users=(reader,))
    admin = backend.admin.permission_writes

    _ = await admin.set_permissions(kind, number, AccessChange(owner=owner))
    owned = await user.permission_writes.set_permissions(
        kind, number, AccessChange(permissions=grants)
    )
    _ = await admin.set_permissions(
        kind, number, AccessChange(owner=None, permissions=ObjectPermissions())
    )
    unowned = await user.permission_writes.set_permissions(
        kind, number, AccessChange(permissions=grants)
    )

    assert (owned.owner, owned.permissions) == (owner, grants)
    assert (unowned.owner, unowned.permissions) == (None, grants)


@pytest.mark.decision("decision-87")
@pytest.mark.contract(SET)
@pytest.mark.parametrize("unowned", [True, False])
async def test_grants_of_a_rule_are_set_when_only_its_unowned_twin_is_refused(
    backend: Backend,
    account: int,
    unowned: bool,  # noqa: FBT001 -- the case
) -> None:
    """Two rules of one owner cannot have the same name: checked first for a user, last for none."""
    ports = backend.admin
    number = await mail_object(backend, "mail_rule", account)
    reader = await user_id(ports, READER[0])
    owner = None if unowned else reader
    name = (await ports.mail.get_mail_rule(number)).name
    _ = await ports.permission_writes.set_permissions(
        "mail_rule", number, AccessChange(owner=owner)
    )
    twin = await ports.mail_writes.create_mail_rule(MailRuleValues(name=name, account_id=account))
    change = AccessChange(owner=owner, permissions=ObjectPermissions(view_users=(reader,)))

    with pytest.raises(Validation):
        _ = await ports.permission_writes.set_permissions("mail_rule", twin.id, change)

    [after] = await ports.permissions.get_permissions("mail_rule", [twin.id])
    assert after.owner == twin.owner
    assert after.permissions.view_users == ((reader,) if unowned else ())


@pytest.mark.decision("decision-87")
@pytest.mark.contract(SET)
async def test_a_change_grant_on_a_rule_is_enough_to_make_it_delete_mail(
    backend: Backend, account: int
) -> None:
    """Without any right on the account, whose saved password the rule then uses."""
    name = f"contract-user-{uuid.uuid4().hex[:12]}"
    user = backend.restricted(frozenset({"view_mailrule", "change_mailrule"}), name=name)
    rule = await mail_object(backend, "mail_rule", account)
    grantee = await user_id(backend.admin, name)
    _ = await backend.admin.permission_writes.set_permissions(
        "mail_rule", rule, AccessChange(permissions=ObjectPermissions(change_users=(grantee,)))
    )

    changed = await user.mail_writes.update_mail_rule(rule, MailRuleValues(action="delete"))

    assert (changed.action, changed.account_id) == ("delete", account)
    with pytest.raises(PermissionDenied):
        _ = await user.mail.get_mail_account(account)


@pytest.mark.contract(SET)
async def test_grants_of_a_tag_are_not_set_when_its_new_owner_is_refused(backend: Backend) -> None:
    ports = backend.admin
    reader = await user_id(ports, READER[0])
    first = await make(ports, "tag")
    tags = {item.id: item.name for item in await ports.taxonomy.list_taxonomy("tag")}
    _ = await ports.permission_writes.set_permissions("tag", first, AccessChange(owner=reader))
    twin = (
        await ports.taxonomy_writes.create_taxonomy_item(
            "tag", tags[first], TaxonomyValues.model_validate({})
        )
    ).id
    change = AccessChange(owner=reader, permissions=ObjectPermissions(view_users=(reader,)))

    with pytest.raises(Validation):
        _ = await ports.permission_writes.set_permissions("tag", twin, change)

    [after] = await ports.permissions.get_permissions("tag", [twin])
    assert after.permissions.view_users == ()
