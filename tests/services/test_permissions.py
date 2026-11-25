"""PermissionService: merge and replace, the confirmation, principals, partial failures, lookups.

The fakes of the "nothing may be written" cases are created without `allow_writes` (see
tests/services/test_bulk_documents.py), so any write on an unconfirmed or invalid path fails.
Seeded ids: documents 1 (public) and 2 (private, owned by the admin); tags 1 to 4; users admin 2,
reader 3, blind 4; group 1.
"""

from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.mail import FakeMailAccount, FakeMailRule
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.permissions import (
    MAX_OBJECTS,
    AccessChange,
    PermissionsRequest,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.permissions import PermissionService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.documents import NameOrId
    from mcp_server_paperless.domain.instance import InstanceOverview
    from mcp_server_paperless.domain.permissions import (
        Mode,
        ObjectAccess,
        PermissionKind,
        PermissionsResult,
    )
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-12"),
    pytest.mark.decision("decision-19"),
    pytest.mark.decision("decision-15"),
]

PUBLIC, PRIVATE = 1, 2
FINANCE, INVOICES = 1, 2
ADMIN_ID, READER_ID, BLIND_ID, GROUP = 2, 3, 4, 1
WEB = "https://paperless.test"
CHANGER = frozenset({"view_document", "change_document", "view_tag", "change_tag"})


class Recording(FakePaperless):
    """A fake that remembers the writes it was asked for and can fail the n-th of them."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.writes: list[tuple[PermissionKind, int, AccessChange]] = []
        self.fail_on: int | None = None
        self.taxonomy_reads: int = 0

    @override
    async def set_permissions(
        self, kind: PermissionKind, object_id: int, change: AccessChange
    ) -> ObjectAccess:
        self.writes.append((kind, object_id, change))
        if self.fail_on == len(self.writes):
            message = "Paperless rejected the request: boom"
            raise Validation(message, hint="Check the ids.")
        return await super().set_permissions(kind, object_id, change)

    @override
    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        self.taxonomy_reads += 1
        return await super().list_taxonomy(kind)


@final
class Setup:
    def __init__(
        self,
        user: str = ADMIN[0],
        *,
        writes: bool = True,
        grant: frozenset[str] | None = None,
        only: frozenset[str] | None = None,
    ) -> None:
        self.state: FakeState = seeded()
        self.state.users = [
            replace(
                u, permissions=only if only is not None else u.permissions | (grant or frozenset())
            )
            if u.username == user
            else u
            for u in self.state.users
        ]
        self.fake: Recording = Recording(self.state, user=user, writes=writes)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.service: PermissionService = PermissionService(
            self.fake, self.fake, self.taxonomy, self.fake, web_url=WEB
        )

    async def run(
        self,
        kind: PermissionKind,
        items: list[NameOrId],
        *,
        mode: Mode = "merge",
        confirm: bool = True,
        **request: object,
    ) -> PermissionsResult:
        asked = PermissionsRequest.model_validate({"mode": mode, **request})
        return await self.service.set_permissions(kind, items, asked, confirm=confirm)

    def document(self, number: int) -> FakeDocument:
        [found] = [d for d in self.state.documents if d.id == number]
        return found


def names(principals: tuple[object, ...]) -> list[str]:
    return [getattr(p, "name") for p in principals]  # noqa: B009 -- the Principal rows


# --- merge


async def test_a_confirmed_merge_adds_grants_and_keeps_the_existing_ones() -> None:
    setup = Setup()
    setup.document(PUBLIC).viewers = {READER_ID}

    result = await setup.run(
        "document", [PUBLIC], view_groups=["e2e-group"], change_users=["e2e-blind"]
    )

    [row] = result.objects
    access = row.access
    assert (row.id, row.name, row.changed) == (PUBLIC, "e2e-public-invoice", True)
    assert row.web_url == f"{WEB}/documents/{PUBLIC}/details"
    assert names(access.view_users) == ["e2e-reader"]
    assert names(access.view_groups) == ["e2e-group"]
    assert names(access.change_users) == ["e2e-blind"]  # a change grant includes view
    assert access.change_groups == ()
    assert access.owner is None
    assert (result.kind, result.mode, result.changed_count, result.unchanged_count) == (
        "document",
        "merge",
        1,
        0,
    )
    document = setup.document(PUBLIC)
    assert (document.viewers, document.editors) == ({READER_ID, BLIND_ID}, {BLIND_ID})
    assert document.view_groups == {GROUP}
    assert result.message == "Changed the permissions of 'e2e-public-invoice' (merge)."


async def test_merge_sends_every_list_whole_and_the_owner_only_when_it_changes() -> None:
    setup = Setup()
    setup.document(PUBLIC).viewers = {READER_ID}

    _ = await setup.run("document", [PUBLIC], change_groups=[GROUP])

    [(kind, number, change)] = setup.fake.writes
    assert (kind, number) == ("document", PUBLIC)
    assert "owner" not in change.model_fields_set
    assert change.permissions is not None
    assert change.permissions.model_dump() == {
        "view_users": (READER_ID,),
        "view_groups": (GROUP,),
        "change_users": (),
        "change_groups": (GROUP,),
    }


async def test_merge_upgrades_a_viewer_to_a_changer_and_never_downgrades() -> None:
    setup = Setup()
    setup.document(PUBLIC).viewers = {READER_ID, BLIND_ID}
    setup.document(PUBLIC).editors = {BLIND_ID}

    result = await setup.run(
        "document", [PUBLIC], change_users=["e2e-reader"], view_users=[BLIND_ID]
    )

    [row] = result.objects
    assert names(row.access.change_users) == ["e2e-reader", "e2e-blind"]  # by id
    assert row.access.view_users == ()


async def test_merge_that_changes_nothing_sends_nothing_and_needs_no_confirm() -> None:
    setup = Setup(writes=False)
    setup.document(PUBLIC).viewers = {READER_ID}

    result = await setup.run("document", [PUBLIC], view_users=["e2e-reader"])

    assert setup.fake.writes == []
    assert (result.changed_count, result.unchanged_count) == (0, 1)
    assert [row.changed for row in result.objects] == [False]
    assert names(result.objects[0].access.view_users) == ["e2e-reader"]
    assert result.message == "Nothing changed: 1 document already is as asked."


async def test_merge_works_on_every_taxonomy_kind_by_name_or_id() -> None:
    setup = Setup()

    result = await setup.run("tag", ["e2e-finance", str(INVOICES)], view_users=[READER_ID])

    assert [row.id for row in result.objects] == [FINANCE, INVOICES]
    assert all(row.web_url is None for row in result.objects)
    assert setup.state.tags[0].viewers == {READER_ID}
    assert setup.state.tags[1].viewers == {READER_ID}
    others: tuple[tuple[PermissionKind, str], ...] = (
        ("correspondent", "e2e-acme"),
        ("document_type", "e2e-invoice"),
        ("storage_path", "e2e-archive"),
    )
    for kind, name in others:
        done = await setup.run(kind, [name], view_groups=["e2e-group"])
        assert names(done.objects[0].access.view_groups) == ["e2e-group"]


async def test_merge_lists_that_are_empty_are_not_a_change() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="Nothing to change"):
        _ = await setup.run("document", [PUBLIC], view_users=[], change_groups=[])


# --- replace


async def test_replace_needs_a_confirmation_that_names_who_loses_and_who_gains() -> None:
    setup = Setup(writes=False)
    document = setup.document(PUBLIC)
    document.viewers, document.editors = {READER_ID, BLIND_ID}, {BLIND_ID}
    document.view_groups = {GROUP}

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run(
            "document", [PUBLIC], mode="replace", view_users=["admin"], confirm=False
        )

    assert refusal.value.message == (
        "Nothing was changed. Confirming would replace the permissions of 1 document "
        "'e2e-public-invoice': gains view: user 'admin'; loses view: user 'e2e-reader'."
    )
    assert refusal.value.hint == CONFIRM_HINT
    assert setup.fake.writes == []


async def test_replace_replaces_only_the_lists_that_are_given() -> None:
    setup = Setup()
    document = setup.document(PUBLIC)
    document.viewers, document.editors = {READER_ID, BLIND_ID}, {BLIND_ID}
    document.view_groups, document.change_groups = {GROUP}, {GROUP}

    result = await setup.run(
        "document", [PUBLIC], mode="replace", confirm=True, view_users=["e2e-reader"]
    )

    [row] = result.objects
    assert names(row.access.view_users) == ["e2e-reader"]
    assert names(row.access.change_users) == ["e2e-blind"]  # not given: kept
    assert names(row.access.change_groups) == ["e2e-group"]  # not given: kept
    assert (document.viewers, document.editors) == ({READER_ID, BLIND_ID}, {BLIND_ID})


async def test_replace_with_an_empty_list_removes_that_list_and_says_so() -> None:
    setup = Setup(writes=False)
    document = setup.document(PUBLIC)
    document.viewers, document.editors = {READER_ID, BLIND_ID}, {BLIND_ID}

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run(
            "document", [PUBLIC], mode="replace", view_users=[], change_users=[], confirm=False
        )

    assert "loses view and change: user 'e2e-blind'" in refusal.value.message
    assert "loses view: user 'e2e-reader'" in refusal.value.message

    writing = Setup()
    writing.document(PUBLIC).viewers = {READER_ID}
    done = await writing.run(
        "document", [PUBLIC], mode="replace", confirm=True, view_users=[], change_users=[]
    )
    assert done.objects[0].access.view_users == ()
    assert writing.document(PUBLIC).viewers == set()


async def test_replace_without_any_list_or_owner_is_refused_before_anything_is_read() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="Nothing to change") as refusal:
        _ = await setup.run("document", [PUBLIC], mode="replace", confirm=True)

    assert "view_users" in (refusal.value.hint or "")
    assert setup.fake.writes == []


async def test_a_replace_that_changes_nothing_needs_no_confirmation() -> None:
    setup = Setup(writes=False)
    setup.document(PUBLIC).viewers = {READER_ID}

    result = await setup.run("document", [PUBLIC], mode="replace", view_users=["e2e-reader"])

    assert (result.changed_count, result.unchanged_count) == (0, 1)
    assert setup.fake.writes == []


async def test_replace_that_only_adds_still_needs_a_confirmation() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired, match="gains view: user 'e2e-reader'"):
        _ = await setup.run(
            "document", [PUBLIC], mode="replace", view_users=["e2e-reader"], confirm=False
        )


# --- owner


async def test_changing_the_owner_needs_a_confirmation_in_either_mode() -> None:
    for mode in ("merge", "replace"):
        setup = Setup(writes=False)
        with pytest.raises(ConfirmationRequired) as refusal:
            _ = await setup.run("document", [PRIVATE], mode=mode, owner="e2e-reader", confirm=False)
        assert refusal.value.message == (
            "Nothing was changed. Confirming would change the permissions of 1 document "
            "'e2e-private-note': the owner changes from 'admin' to 'e2e-reader' "
            "('admin' loses the control ownership gave; the new owner controls it; you may "
            "lose access to it)."
        )
        assert setup.fake.writes == []


async def test_confirmed_the_owner_is_changed_and_sent_with_no_grants() -> None:
    setup = Setup()

    result = await setup.run("document", [PRIVATE], owner=READER_ID, confirm=True)

    assert setup.document(PRIVATE).owner == READER_ID
    assert result.objects[0].access.owner is not None
    assert result.objects[0].access.owner.name == "e2e-reader"
    [(_, _, change)] = setup.fake.writes
    assert change.model_fields_set == {"owner"}
    assert change.owner == READER_ID


async def test_clearing_the_owner_needs_a_confirmation_that_says_who_gets_access() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run("document", [PRIVATE], clear_owner=True, confirm=False)

    assert refusal.value.message == (
        "Nothing was changed. Confirming would change the permissions of 1 document "
        "'e2e-private-note': the owner 'admin' is removed ('admin' loses the control ownership "
        "gave; every user with the global permission to view documents can then see it and every "
        "user with the global permission to change documents can change it)."
    )

    writing = Setup()
    done = await writing.run("document", [PRIVATE], clear_owner=True, confirm=True)
    assert done.objects[0].access.owner is None
    assert writing.document(PRIVATE).owner is None


async def test_an_owner_that_already_is_as_asked_needs_no_confirmation_and_sends_nothing() -> None:
    setup = Setup(writes=False)

    owned = await setup.run("document", [PRIVATE], owner="admin")
    free = await setup.run("document", [PUBLIC], clear_owner=True)

    assert (owned.changed_count, free.changed_count) == (0, 0)
    assert setup.fake.writes == []


async def test_owner_and_grants_go_in_one_write_and_one_refusal() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run(
            "document", [PRIVATE], owner="e2e-reader", view_users=[BLIND_ID], confirm=False
        )

    assert "the owner changes from 'admin' to 'e2e-reader'" in refusal.value.message
    assert "gains view: user 'e2e-blind'" in refusal.value.message

    writing = Setup()
    _ = await writing.run(
        "document", [PRIVATE], owner="e2e-reader", view_users=[BLIND_ID], confirm=True
    )
    assert len(writing.fake.writes) == 1


async def test_owner_and_clear_owner_together_are_refused() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="owner or clear_owner, not both"):
        _ = await setup.run("document", [PRIVATE], owner="admin", clear_owner=True)


# --- who and what is named


async def test_an_unknown_user_or_group_fails_before_anything_is_sent() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound, match="No user is named 'ghost'"):
        _ = await setup.run("document", [PUBLIC], view_users=["e2e-reader", "ghost"])
    with pytest.raises(NotFound, match="No group is named 'nobody'"):
        _ = await setup.run("document", [PUBLIC], change_groups=["nobody"])
    with pytest.raises(NotFound, match="No user has id 99"):
        _ = await setup.run("document", [PUBLIC], owner=99, confirm=True)
    assert setup.fake.writes == []


async def test_a_user_that_matches_more_than_one_is_ambiguous() -> None:
    setup = Setup(writes=False)
    setup.state.users.append(replace(setup.state.users[1], id=9, username="E2E-READER"))

    with pytest.raises(Ambiguous):
        _ = await setup.run("document", [PUBLIC], view_users=["E2E-Reader"])


async def test_an_object_the_user_may_not_see_or_that_does_not_exist_is_not_found() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound) as refusal:
        _ = await setup.run("document", [PUBLIC, 99, 100], view_users=[READER_ID])

    assert refusal.value.message == ("No document with ids 99, 100 exists that you may see.")
    reader = Setup(READER[0], writes=False)
    with pytest.raises(NotFound, match="id 2 exists"):
        _ = await reader.run("document", [PRIVATE], view_users=[READER_ID])
    assert setup.fake.writes == []


async def test_documents_are_named_by_id_not_by_title() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="Documents are selected by id") as refusal:
        _ = await setup.run("document", ["e2e-public-invoice"], view_users=[READER_ID])

    assert "search_documents" in (refusal.value.hint or "")


async def test_a_taxonomy_name_that_matches_nothing_is_not_found() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound, match="No tag is named 'nope'"):
        _ = await setup.run("tag", ["nope"], view_users=[READER_ID])


async def test_the_selection_is_between_one_and_the_limit_and_repeats_count_once() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match=f"between 1 and {MAX_OBJECTS}"):
        _ = await setup.run("document", [], view_users=[READER_ID])
    with pytest.raises(Validation, match=f"between 1 and {MAX_OBJECTS}"):
        _ = await setup.run("document", list(range(1, MAX_OBJECTS + 2)), view_users=[READER_ID])

    writing = Setup()
    result = await writing.run("document", [PUBLIC, PUBLIC, str(PUBLIC)], view_users=[READER_ID])
    assert [row.id for row in result.objects] == [PUBLIC]
    assert len(writing.fake.writes) == 1


async def test_a_user_who_may_not_list_users_can_pass_ids_but_not_names() -> None:
    setup = Setup(READER[0], writes=True, grant=CHANGER)
    setup.document(PUBLIC).owner = READER_ID

    with pytest.raises(PermissionDenied) as refusal:
        _ = await setup.run("document", [PUBLIC], view_users=["e2e-blind"])
    assert refusal.value.hint == "Pass the user id instead of the name."

    result = await setup.run("document", [PUBLIC], view_users=[BLIND_ID])

    [row] = result.objects
    assert [(p.id, p.name) for p in row.access.view_users] == [(BLIND_ID, f"#{BLIND_ID}")]
    assert row.access.owner is not None
    assert row.access.owner.name == f"#{READER_ID}"


async def test_an_id_that_does_not_exist_is_a_validation_error_when_the_list_is_closed() -> None:
    setup = Setup(READER[0], writes=True, grant=CHANGER)
    setup.document(PUBLIC).owner = READER_ID

    with pytest.raises(Validation, match="don't exist"):
        _ = await setup.run("document", [PUBLIC], view_users=[99])


# --- permissions of the caller


async def test_a_user_who_does_not_own_the_object_is_refused_by_paperless() -> None:
    setup = Setup(READER[0], writes=True, grant=CHANGER)
    setup.document(PUBLIC).owner = ADMIN_ID
    setup.document(PUBLIC).editors = {READER_ID}
    setup.document(PUBLIC).viewers = {READER_ID}

    with pytest.raises(PermissionDenied):
        _ = await setup.run("document", [PUBLIC], view_users=[BLIND_ID])


# --- several objects, partial failure, caches


async def test_a_failure_halfway_says_what_was_already_changed() -> None:
    setup = Setup()
    setup.fake.fail_on = 2

    with pytest.raises(Validation) as failure:
        _ = await setup.run("tag", [FINANCE, INVOICES, 3], view_users=[READER_ID])

    assert failure.value.message == (
        "Paperless rejected the request: boom Permissions were already changed on 'e2e-finance'. "
        "'e2e-invoices' and the objects after it were not changed."
    )
    assert setup.state.tags[0].viewers == {READER_ID}
    assert setup.state.tags[1].viewers == set()


async def test_a_failure_on_the_first_object_changed_nothing_and_says_nothing_extra() -> None:
    setup = Setup()
    setup.fake.fail_on = 1

    with pytest.raises(Validation) as failure:
        _ = await setup.run("tag", [FINANCE, INVOICES], view_users=[READER_ID])

    assert failure.value.message == "Paperless rejected the request: boom"


async def test_objects_that_already_are_as_asked_are_left_alone_among_others() -> None:
    setup = Setup()
    setup.state.tags[0].viewers = {READER_ID}

    result = await setup.run("tag", [FINANCE, INVOICES], view_users=[READER_ID])

    assert [(row.id, row.changed) for row in result.objects] == [(FINANCE, False), (INVOICES, True)]
    assert [number for _, number, _ in setup.fake.writes] == [INVOICES]
    assert (result.changed_count, result.unchanged_count) == (1, 1)


async def test_the_confirmation_names_the_first_objects_and_counts_the_rest() -> None:
    setup = Setup(writes=False)
    for number in range(10, 18):
        setup.state.documents.append(FakeDocument(id=number, title=f"doc-{number}"))

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run(
            "document", list(range(10, 18)), mode="replace", view_users=[READER_ID], confirm=False
        )

    message = refusal.value.message
    assert (
        "replace the permissions of 8 documents 'doc-10', 'doc-11', 'doc-12', 'doc-13', " in message
    )
    assert "'doc-14' and 3 more: 'doc-10': gains view: user 'e2e-reader'." in message
    assert "'doc-14': gains view" in message
    assert "'doc-15'" not in message.split(" more: ")[1]  # the details name five objects


async def test_a_taxonomy_write_makes_the_resolver_read_the_kind_again() -> None:
    setup = Setup()
    _ = await setup.run("tag", [FINANCE], view_users=[READER_ID])
    reads = setup.fake.taxonomy_reads

    _ = await setup.taxonomy.items("tag")
    assert setup.fake.taxonomy_reads == reads + 1

    _ = await setup.taxonomy.items("tag")
    assert setup.fake.taxonomy_reads == reads + 1  # now cached again


async def test_a_failed_write_also_makes_the_resolver_forget() -> None:
    setup = Setup()
    setup.fake.fail_on = 1

    with pytest.raises(Validation):
        _ = await setup.run("tag", [FINANCE], view_users=[READER_ID])
    reads = setup.fake.taxonomy_reads
    _ = await setup.taxonomy.items("tag")

    assert setup.fake.taxonomy_reads == reads + 1


async def test_a_document_write_makes_the_resolver_read_the_counted_kinds_again() -> None:
    setup = Setup()
    for kind in ("tag", "correspondent", "document_type", "storage_path", "user"):
        _ = await setup.taxonomy.items(kind)
    _ = await setup.run("document", [PUBLIC], view_users=[READER_ID])
    reads = setup.fake.taxonomy_reads

    for kind in ("tag", "correspondent", "document_type", "storage_path"):
        _ = await setup.taxonomy.items(kind)
    assert setup.fake.taxonomy_reads == reads + 4
    _ = await setup.taxonomy.items("user")
    assert setup.fake.taxonomy_reads == reads + 4  # users are not counted


# --- lookups


async def test_users_are_listed_compactly_with_paging_and_a_name_filter() -> None:
    setup = Setup(writes=False)

    page = await setup.service.list_users(name_contains=None, page=1, page_size=2)
    rest = await setup.service.list_users(name_contains=None, page=2, page_size=2)
    found = await setup.service.list_users(name_contains="E2E-R", page=1, page_size=25)

    assert (page.count, page.page, page.next_page) == (3, 1, 2)
    assert [u.username for u in page.users] == ["admin", "e2e-blind"]
    assert (rest.next_page, [u.username for u in rest.users]) == (None, ["e2e-reader"])
    [reader] = found.users
    assert (reader.id, reader.is_active, reader.is_superuser, reader.group_ids) == (
        READER_ID,
        True,
        False,
        (),
    )
    assert page.users[0].is_superuser


async def test_groups_are_listed_compactly() -> None:
    setup = Setup(writes=False)

    page = await setup.service.list_groups(name_contains="group", page=1, page_size=25)
    none = await setup.service.list_groups(name_contains="zzz", page=1, page_size=25)

    assert [(g.id, g.name) for g in page.groups] == [(GROUP, "e2e-group")]
    assert (page.count, page.next_page) == (1, None)
    assert (none.count, none.groups) == (0, ())


async def test_listing_users_needs_the_permission_to_view_them() -> None:
    reader = Setup(READER[0], writes=False)

    with pytest.raises(PermissionDenied) as users:
        _ = await reader.service.list_users(name_contains=None, page=1, page_size=25)
    with pytest.raises(PermissionDenied) as groups:
        _ = await reader.service.list_groups(name_contains=None, page=1, page_size=25)
    assert "Listing users needs the view permission" in (users.value.hint or "")
    assert "Listing groups needs the view permission" in (groups.value.hint or "")


# --- messages and details (pinned down one by one)


async def test_a_merge_into_groups_keeps_the_groups_that_are_there() -> None:
    setup = Setup()
    setup.state.groups.append(replace(setup.state.groups[0], id=5, name="e2e-other"))
    setup.document(PUBLIC).view_groups = {GROUP}
    setup.document(PUBLIC).change_groups = {GROUP}

    result = await setup.run("document", [PUBLIC], view_groups=["e2e-other"], change_groups=[5])

    [row] = result.objects
    assert names(row.access.change_groups) == ["e2e-group", "e2e-other"]
    assert names(row.access.view_groups) == []


async def test_the_result_counts_what_was_left_alone() -> None:
    one = Setup()
    one.document(PUBLIC).viewers = {READER_ID}
    mixed = await one.run("document", [PUBLIC, PRIVATE], view_users=[READER_ID])

    many = Setup()
    for number in (PUBLIC, PRIVATE):
        many.document(number).viewers = {READER_ID}
    many.state.documents.append(FakeDocument(id=30, title="third"))
    three = await many.run("document", [PUBLIC, PRIVATE, 30], view_users=[READER_ID])
    both = await many.run("document", [PUBLIC, PRIVATE, 30], view_users=[READER_ID])

    assert mixed.message == (
        "Changed the permissions of 'e2e-private-note' (merge). 1 already was as asked."
    )
    assert three.message == ("Changed the permissions of 'third' (merge). 2 already were as asked.")
    assert both.message == "Nothing changed: 3 documents already are as asked."
    assert (mixed.changed_count, mixed.unchanged_count) == (1, 1)


async def test_the_refusal_names_each_object_once_there_are_two_and_counts_beyond_five() -> None:
    two = Setup(writes=False)
    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await two.run(
            "document", [PUBLIC, PRIVATE], mode="replace", view_users=[READER_ID], confirm=False
        )
    assert refusal.value.message == (
        "Nothing was changed. Confirming would replace the permissions of 2 documents "
        "'e2e-public-invoice', 'e2e-private-note': 'e2e-public-invoice': gains view: user "
        "'e2e-reader'. 'e2e-private-note': gains view: user 'e2e-reader'."
    )

    five = Setup(writes=False)
    for number in range(10, 13):
        five.state.documents.append(FakeDocument(id=number, title=f"doc-{number}"))
    ids: list[NameOrId] = [PUBLIC, PRIVATE, 10, 11, 12]
    with pytest.raises(ConfirmationRequired) as exact:
        _ = await five.run("document", ids, mode="replace", view_users=[READER_ID], confirm=False)
    assert " more" not in exact.value.message.split("'doc-12'", 1)[1]
    assert "'doc-12': gains view: user 'e2e-reader'." in exact.value.message

    eight = Setup(writes=False)
    for number in range(10, 16):
        eight.state.documents.append(FakeDocument(id=number, title=f"doc-{number}"))
    with pytest.raises(ConfirmationRequired) as over:
        _ = await eight.run(
            "document",
            [PUBLIC, PRIVATE, *range(10, 16)],
            mode="replace",
            view_users=[READER_ID],
            confirm=False,
        )
    assert over.value.message.endswith(". 3 more would change as well.")


async def test_a_missing_object_has_a_hint_that_says_where_to_look() -> None:
    setup = Setup(writes=False)
    _ = await setup.taxonomy.items("tag")  # the resolver still knows the tags ...
    setup.state.tags = []  # ... when they are gone for the reader of the grants

    with pytest.raises(NotFound) as document:
        _ = await setup.run("document", [99], view_users=[READER_ID])
    with pytest.raises(NotFound) as tag:
        _ = await setup.run("tag", [FINANCE, INVOICES], view_users=[READER_ID])

    assert document.value.message == "No document with id 99 exists that you may see."
    assert document.value.hint == "Find the ids with search_documents."
    assert tag.value.message == "No tag with ids 1, 2 exists that you may see."
    assert tag.value.hint == "Call list_taxonomy to see what exists."


async def test_the_refusal_names_users_and_groups_who_gain_or_lose_and_the_unowned_start() -> None:
    setup = Setup(writes=False)
    document = setup.document(PUBLIC)
    document.viewers, document.editors = {READER_ID, BLIND_ID}, {BLIND_ID}
    document.view_groups, document.change_groups = {GROUP}, {GROUP}

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run(
            "document",
            [PUBLIC],
            mode="replace",
            owner="e2e-reader",
            view_users=["admin"],
            change_users=[],
            change_groups=[],
            confirm=False,
        )

    assert refusal.value.message == (
        "Nothing was changed. Confirming would replace the permissions of 1 document "
        "'e2e-public-invoice': the owner changes from none to 'e2e-reader' (the document stops "
        "being visible to everyone who saw it through the global permission; the new owner "
        "controls it; you may lose access to it); gains view: user 'admin'; "
        "loses change (keeps view): group 'e2e-group'; "
        "loses view: user 'e2e-reader'; loses view and change: user 'e2e-blind'."
    )


async def test_several_people_in_one_bucket_are_separated_by_commas() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired, match="gains view: user 'admin', user 'e2e-blind'"):
        _ = await setup.run(
            "document", [PUBLIC], mode="replace", view_users=["admin", "e2e-blind"], confirm=False
        )


async def test_the_lookup_errors_keep_the_message_of_paperless_and_groups_page() -> None:
    reader = Setup(READER[0], writes=False)
    with pytest.raises(PermissionDenied) as denied:
        _ = await reader.service.list_users(name_contains=None, page=1, page_size=25)
    with pytest.raises(PermissionDenied) as groups:
        _ = await reader.service.list_groups(name_contains=None, page=1, page_size=25)
    assert denied.value.message == "You do not have permission to perform this action."
    assert groups.value.message == "You do not have permission to perform this action."

    setup = Setup(writes=False)
    setup.state.groups.append(replace(setup.state.groups[0], id=5, name="e2e-other"))
    first = await setup.service.list_groups(name_contains=None, page=1, page_size=1)
    assert (first.count, first.next_page, [g.name for g in first.groups]) == (2, 2, ["e2e-group"])


async def test_the_service_asks_for_confirm_by_default() -> None:
    setup = Setup(writes=False)
    asked = PermissionsRequest(mode="replace", view_users=[READER_ID])

    with pytest.raises(ConfirmationRequired):
        _ = await setup.service.set_permissions("document", [PUBLIC], asked)


async def test_the_errors_of_a_bad_request_carry_their_hints() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as both:
        _ = await setup.run("document", [PUBLIC], owner="admin", clear_owner=True)
    with pytest.raises(Validation) as empty:
        _ = await setup.run("document", [PUBLIC], view_users=[])
    with pytest.raises(Validation) as none:
        _ = await setup.run("document", [PUBLIC])
    with pytest.raises(Validation) as many:
        _ = await setup.run("document", [], view_users=[READER_ID])

    assert both.value.message == "Give owner or clear_owner, not both."
    assert both.value.hint == "clear_owner=true removes the owner."
    assert empty.value.message == "Nothing to change: every list is empty and merge only adds."
    assert none.value.message == "Nothing to change: no owner and no grants were given."
    for refusal in (empty, none):
        assert refusal.value.hint == (
            "Pass owner, clear_owner, or users or groups in view_users, view_groups, "
            "change_users or change_groups (with mode replace an empty list removes that "
            "list's grants)."
        )
    assert many.value.message == "Select between 1 and 100 objects per call."
    assert many.value.hint == "Work in several steps."


async def test_exactly_the_limit_of_objects_is_a_selection_and_one_more_is_not() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound):  # past the checks: none of these documents exists
        _ = await setup.run("document", list(range(1, MAX_OBJECTS + 1)), view_users=[READER_ID])
    with pytest.raises(Validation, match="between 1 and 100"):
        _ = await setup.run("document", list(range(1, MAX_OBJECTS + 2)), view_users=[READER_ID])


async def test_a_title_is_refused_with_a_hint_to_search() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as refusal:
        _ = await setup.run("document", ["a title"], view_users=[READER_ID])

    assert refusal.value.message == "Documents are selected by id, not by title."
    assert refusal.value.hint == "Find the ids with search_documents."


async def test_the_refusal_names_the_groups_of_every_object() -> None:
    setup = Setup(writes=False)
    setup.state.groups.append(replace(setup.state.groups[0], id=5, name="e2e-other"))
    setup.document(PUBLIC).view_groups = {GROUP}
    setup.document(PRIVATE).view_groups = {5}

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run(
            "document",
            [PUBLIC, PRIVATE],
            mode="replace",
            view_groups=[],
            view_users=[READER_ID],
            confirm=False,
        )

    message = refusal.value.message
    assert (
        "'e2e-public-invoice': gains view: user 'e2e-reader'; loses view: group 'e2e-group'."
        in message
    )
    assert (
        "'e2e-private-note': gains view: user 'e2e-reader'; loses view: group 'e2e-other'."
        in message
    )


async def test_the_refusal_names_the_groups_the_new_lists_add() -> None:
    setup = Setup(writes=False)
    setup.document(PUBLIC).view_groups = {GROUP}

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run(
            "document", [PUBLIC], mode="replace", change_groups=[GROUP], confirm=False
        )

    assert refusal.value.message.endswith("gains change: group 'e2e-group'.")


async def test_a_failure_halfway_keeps_the_hint_of_the_error() -> None:
    setup = Setup()
    setup.fake.fail_on = 2

    with pytest.raises(Validation) as failure:
        _ = await setup.run("tag", [FINANCE, INVOICES], view_users=[READER_ID])

    assert failure.value.hint == "Check the ids."
    assert type(failure.value) is Validation


async def test_a_tag_write_forgets_tags_only_and_a_document_write_the_counted_kinds() -> None:
    setup = Setup()
    for kind in ("tag", "correspondent"):
        _ = await setup.taxonomy.items(kind)
    _ = await setup.run("tag", [FINANCE], view_users=[READER_ID])
    reads = setup.fake.taxonomy_reads

    _ = await setup.taxonomy.items("correspondent")
    assert setup.fake.taxonomy_reads == reads  # untouched: still cached
    _ = await setup.taxonomy.items("tag")
    assert setup.fake.taxonomy_reads == reads + 1


# --- every change is confirmed (decision-19), the owner texts, uncertain failures


async def test_a_merge_that_adds_a_grant_is_refused_without_confirm_and_writes_nothing() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run("document", [PUBLIC], view_users=["e2e-reader"], confirm=False)
    with pytest.raises(ConfirmationRequired):
        _ = await setup.run("tag", ["e2e-finance"], change_groups=[GROUP], confirm=False)

    assert refusal.value.message == (
        "Nothing was changed. Confirming would change the permissions of 1 document "
        "'e2e-public-invoice': gains view: user 'e2e-reader'."
    )
    assert refusal.value.hint == CONFIRM_HINT
    assert setup.fake.writes == []
    assert setup.document(PUBLIC).viewers == set()


async def test_only_a_call_that_changes_nothing_needs_no_confirm() -> None:
    setup = Setup(writes=False)
    setup.document(PUBLIC).viewers = {READER_ID}

    result = await setup.run("document", [PUBLIC], view_users=["e2e-reader"], confirm=False)

    assert result.changed_count == 0
    assert setup.fake.writes == []


async def test_handing_an_object_to_the_caller_does_not_warn_about_losing_access() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run("document", [PUBLIC], owner="admin", confirm=False)

    assert refusal.value.message == (
        "Nothing was changed. Confirming would change the permissions of 1 document "
        "'e2e-public-invoice': the owner changes from none to 'admin' (the document stops being "
        "visible to everyone who saw it through the global permission; the new owner controls it)."
    )


async def test_a_caller_paperless_does_not_name_is_still_warned() -> None:
    # Without view_uisettings Paperless does not say who the caller is.
    setup = Setup(
        "e2e-reader",
        writes=False,
        only=frozenset({"view_document", "change_document", "view_user"}),
    )
    setup.document(PUBLIC).owner = READER_ID

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await setup.run("document", [PUBLIC], owner="admin", confirm=False)

    assert refusal.value.message.endswith("the new owner controls it; you may lose access to it).")


async def test_an_upstream_failure_says_the_object_may_have_changed_and_keeps_the_status() -> None:
    timeout, busy = "Paperless did not respond in time.", "Paperless is busy."
    odd = "Paperless sent an unexpected response for PATCH /x."

    class Flaky(Recording):
        @override
        async def set_permissions(
            self, kind: PermissionKind, object_id: int, change: AccessChange
        ) -> ObjectAccess:
            if object_id == INVOICES:
                raise UpstreamTimeout(timeout, hint="Try later.")
            if object_id == 3:
                raise UpstreamUnavailable(busy, status=503)
            if object_id == 4:
                raise PaperlessMcpError(odd)
            return await super().set_permissions(kind, object_id, change)

    setup = Setup()
    flaky = Flaky(setup.state, user=ADMIN[0], writes=True)
    service = PermissionService(flaky, flaky, setup.taxonomy, flaky, web_url=WEB)
    asked = PermissionsRequest(view_users=[READER_ID])

    with pytest.raises(UpstreamTimeout) as second:
        _ = await service.set_permissions("tag", [FINANCE, INVOICES], asked, confirm=True)
    with pytest.raises(UpstreamUnavailable) as first:
        _ = await service.set_permissions("tag", [3], asked, confirm=True)
    with pytest.raises(PaperlessMcpError) as strange:
        _ = await service.set_permissions("tag", [4], asked, confirm=True)

    unknown = (
        "Whether the change to {} was applied is unknown, because the request is not retried; "
        "read its permissions before trying again. The objects after it were not changed."
    )
    assert second.value.message == (
        "Paperless did not respond in time. Permissions were already changed on 'e2e-finance'. "
        + unknown.format("'e2e-invoices'")
    )
    assert second.value.hint == "Try later."
    assert (first.value.message, first.value.status) == (
        "Paperless is busy. " + unknown.format("'e2e-private'"),
        503,
    )
    assert type(strange.value) is PaperlessMcpError
    assert strange.value.message == odd + " " + unknown.format("'e2e-inbox'")


async def test_a_caller_lookup_that_fails_leaves_the_warning_in() -> None:
    class Failing(Recording):
        @override
        async def get_overview(self) -> InstanceOverview:
            message = "Paperless is busy."
            raise UpstreamUnavailable(message)

    setup = Setup(writes=False)
    broken = Failing(setup.state, user=ADMIN[0], writes=False)
    service = PermissionService(setup.fake, setup.fake, setup.taxonomy, broken, web_url=WEB)
    asked = PermissionsRequest(owner="admin")

    with pytest.raises(ConfirmationRequired) as refusal:
        _ = await service.set_permissions("document", [PUBLIC], asked)

    assert refusal.value.message.endswith("the new owner controls it; you may lose access to it).")


# --- mail accounts and rules


ACCOUNT_NOTE = (
    "Whoever may change a mail account can make Paperless send its saved password or token to a "
    "server of their choice, at once and without storing anything, and can add rules that import, "
    "move or delete its mail. A grant on an account is none on its rules"
)
RULE_NOTE = (
    "Whoever may change a mail rule can make it move or delete mail with the saved password of its "
    "account, without any right on the account. The owner of a rule owns the documents it imports "
    "from then on; without an owner every user with the global permission to view documents sees "
    "them"
)


def with_mail(setup: Setup) -> None:
    setup.state.mail_accounts.append(
        FakeMailAccount(
            id=5, name="Work", imap_server="h", imap_port=1, username="u", password="p", owner=2
        )
    )
    setup.state.mail_rules.append(FakeMailRule(id=6, name="Bills", account=5, owner=2))
    setup.state.mail_rules.append(FakeMailRule(id=7, name="Bills", account=5, owner=BLIND_ID))


@pytest.mark.decision("decision-87")
async def test_a_change_grant_on_an_account_says_what_it_gives() -> None:
    setup = Setup(writes=False)
    with_mail(setup)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run("mail_account", [5], change_users=["e2e-reader"], confirm=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would change the permissions of 1 mail account 'Work': "
        f"gains change: user 'e2e-reader'. {ACCOUNT_NOTE}."
    )


@pytest.mark.decision("decision-87")
@pytest.mark.parametrize(
    ("request_", "noted"),
    [
        ({"view_users": ["e2e-reader"]}, False),
        ({"change_groups": ["e2e-group"]}, True),
        ({"owner": "e2e-reader"}, True),
        ({"clear_owner": True}, True),
    ],
)
async def test_the_note_stands_where_somebody_may_change_the_account(
    request_: dict[str, object],
    noted: bool,  # noqa: FBT001 -- the case
) -> None:
    setup = Setup(writes=False)
    with_mail(setup)

    asked = PermissionsRequest.model_validate(request_)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.set_permissions("mail_account", [5], asked)
    with pytest.raises(ConfirmationRequired) as ruled:
        _ = await setup.service.set_permissions("mail_rule", ["6"], asked)

    assert (ACCOUNT_NOTE in raised.value.message) == noted
    assert (RULE_NOTE in ruled.value.message) == noted
    assert ACCOUNT_NOTE not in ruled.value.message
    assert RULE_NOTE not in raised.value.message


@pytest.mark.decision("decision-87")
async def test_losing_change_needs_no_note() -> None:
    setup = Setup(writes=False)
    with_mail(setup)
    setup.state.mail_accounts[0].editors = {READER_ID}

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run("mail_account", [5], mode="replace", change_users=[], confirm=False)

    assert "loses" in raised.value.message
    assert ACCOUNT_NOTE not in raised.value.message


@pytest.mark.decision("decision-87")
async def test_confirmed_mail_objects_get_their_owner_and_grants_and_no_link() -> None:
    setup = Setup()
    with_mail(setup)

    account = await setup.run("mail_account", ["5"], change_users=["e2e-reader"])
    rule = await setup.run("mail_rule", [6], owner="e2e-reader")

    [row] = account.objects
    assert (row.id, row.name, row.web_url, row.changed) == (5, "Work", None, True)
    assert names(row.access.change_users) == ["e2e-reader"]
    assert setup.state.mail_accounts[0].editors == {READER_ID}
    assert rule.objects[0].access.owner is not None
    assert setup.state.mail_rules[0].owner == READER_ID
    assert account.message == "Changed the permissions of 'Work' (merge)."


@pytest.mark.decision("decision-87")
@pytest.mark.parametrize(
    ("kind", "label", "tool"),
    [
        ("mail_account", "Mail accounts", "list_mail_accounts"),
        ("mail_rule", "Mail rules", "list_mail_rules"),
    ],
)
async def test_mail_objects_are_chosen_by_id_only(
    kind: PermissionKind, label: str, tool: str
) -> None:
    """Two rules of different owners can have the same name."""
    setup = Setup(writes=False)
    with_mail(setup)

    with pytest.raises(Validation) as named:
        _ = await setup.run(kind, ["Work"], view_users=["e2e-reader"])
    with pytest.raises(NotFound) as missing:
        _ = await setup.run(kind, [99], view_users=["e2e-reader"])

    assert named.value.describe() == (
        f"{label} are selected by id, not by name. Hint: Find the ids with {tool}."
    )
    assert missing.value.describe() == (
        f"No {label.lower().removesuffix('s')} with id 99 exists that you may see. Hint: Find the "
        f"ids with {tool}."
    )


@pytest.mark.decision("decision-87")
async def test_a_view_grant_beside_an_editor_needs_no_note() -> None:
    setup = Setup(writes=False)
    with_mail(setup)
    setup.state.mail_accounts[0].editors = {READER_ID}
    setup.state.mail_accounts[0].change_groups = {GROUP}

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run("mail_account", [5], view_users=["e2e-blind"], confirm=False)

    assert ACCOUNT_NOTE not in raised.value.message


@pytest.mark.decision("decision-87")
@pytest.mark.parametrize("risky", [1, 2, 6])
async def test_the_note_stands_whichever_account_it_is_about(risky: int) -> None:
    """Also one of those the confirmation only counts."""
    setup = Setup(writes=False)
    for number in range(1, 7):
        setup.state.mail_accounts.append(
            FakeMailAccount(
                id=number,
                name=f"A{number}",
                imap_server="h",
                imap_port=1,
                username="u",
                password="p",
                owner=2,
                # The others have the change grant already and only get a view grant.
                editors=set() if number == risky else {READER_ID},
            )
        )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run(
            "mail_account",
            list(range(1, 7)),
            change_users=["e2e-reader"],
            view_users=["e2e-blind"],
            confirm=False,
        )

    assert raised.value.message.count(ACCOUNT_NOTE) == 1


class Clashing(Recording):
    """A Paperless that sets the grants and then refuses the owner."""

    sets: bool = True
    silent: bool = False

    @override
    async def set_permissions(
        self, kind: PermissionKind, object_id: int, change: AccessChange
    ) -> ObjectAccess:
        if self.sets:
            grants = AccessChange(permissions=change.permissions)
            _ = await super().set_permissions(kind, object_id, grants)
        message = "Paperless rejected the request: owner / name unique constraint."
        raise Validation(message, hint="Check the ids.")

    @override
    async def get_permissions(
        self, kind: PermissionKind, object_ids: Sequence[int]
    ) -> list[ObjectAccess]:
        if self.writes and self.silent:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        return await super().get_permissions(kind, object_ids)


@pytest.mark.decision("decision-87")
@pytest.mark.parametrize("kind", ["mail_rule", "tag"])
@pytest.mark.parametrize(
    ("sets", "silent", "said"),
    [
        (True, False, " The grants of {} were set all the same; its owner was not changed."),
        (False, False, ""),
        (True, True, " Whether the grants of {} were set all the same is not known."),
    ],
)
async def test_a_refused_owner_is_told_with_what_became_of_the_grants(
    kind: PermissionKind,
    sets: bool,  # noqa: FBT001 -- the case
    silent: bool,  # noqa: FBT001 -- the case
    said: str,
) -> None:
    """Paperless sets the grants of some kinds before it checks the owner, without a transaction."""
    setup = Setup()
    with_mail(setup)
    fake = Clashing(setup.state, user=ADMIN[0], writes=True)
    fake.sets, fake.silent = sets, silent
    service = PermissionService(fake, fake, setup.taxonomy, fake, web_url=WEB)
    number = 6 if kind == "mail_rule" else FINANCE
    name = "'Bills'" if kind == "mail_rule" else "'e2e-finance'"
    if not sets:
        fake.writes.append((kind, number, AccessChange()))

    asked = PermissionsRequest(owner="e2e-reader", view_users=["e2e-blind"])
    with pytest.raises(Validation) as raised:
        _ = await service.set_permissions(kind, [number], asked, confirm=True)

    assert raised.value.message == (
        "Paperless rejected the request: owner / name unique constraint." + said.format(name)
    )
    assert raised.value.hint == "Check the ids."


@pytest.mark.decision("decision-87")
async def test_a_refused_owner_alone_is_not_read_again() -> None:
    setup = Setup()
    with_mail(setup)
    fake = Clashing(setup.state, user=ADMIN[0], writes=True)
    fake.sets = False
    service = PermissionService(fake, fake, setup.taxonomy, fake, web_url=WEB)

    with pytest.raises(Validation) as raised:
        _ = await service.set_permissions(
            "mail_rule", [6], PermissionsRequest(owner="e2e-reader"), confirm=True
        )

    assert raised.value.message.endswith("unique constraint.")


@final
class Slow(Recording):
    """Each read or write of a mail object takes 25 seconds."""

    now: float = 0.0
    slow: str = "get"

    @override
    async def get_permissions(
        self, kind: PermissionKind, object_ids: Sequence[int]
    ) -> list[ObjectAccess]:
        self.now += 25 if self.slow == "get" else 0
        return await super().get_permissions(kind, object_ids)

    @override
    async def set_permissions(
        self, kind: PermissionKind, object_id: int, change: AccessChange
    ) -> ObjectAccess:
        self.now += 25 if self.slow == "set" else 0
        return await super().set_permissions(kind, object_id, change)


def many_rules(setup: Setup) -> None:
    for number in range(1, 5):
        setup.state.mail_rules.append(
            FakeMailRule(id=number, name=f"R{number}", account=1, owner=2)
        )


@pytest.mark.decision("decision-87")
@pytest.mark.parametrize("slow", ["get", "set"])
async def test_mail_objects_are_read_and_changed_for_at_most_forty_seconds(slow: str) -> None:
    setup = Setup()
    many_rules(setup)
    fake = Slow(setup.state, user=ADMIN[0], writes=True)
    fake.slow = slow
    service = PermissionService(
        fake, fake, setup.taxonomy, fake, web_url=WEB, clock=lambda: fake.now
    )

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await service.set_permissions(
            "mail_rule", [1, 2, 3, 4], PermissionsRequest(view_users=["e2e-reader"]), confirm=True
        )

    if slow == "get":
        assert raised.value.message == (
            "The objects could not be read within 40 seconds. Nothing was changed."
        )
    else:
        assert raised.value.message == (
            "40 seconds have passed. Permissions were already changed on 'R1', 'R2'. 'R3' and "
            "the objects after it were not changed."
        )
    assert raised.value.hint == "Choose fewer objects per call."
    assert len(fake.writes) == (0 if slow == "get" else 2)


async def test_other_kinds_have_no_such_bound() -> None:
    setup = Setup()
    fake = Slow(setup.state, user=ADMIN[0], writes=True)
    fake.slow = "set"
    service = PermissionService(
        fake, fake, setup.taxonomy, fake, web_url=WEB, clock=lambda: fake.now
    )

    done = await service.set_permissions(
        "tag", [1, 2, 3], PermissionsRequest(view_users=["e2e-reader"]), confirm=True
    )

    assert done.changed_count == 3
