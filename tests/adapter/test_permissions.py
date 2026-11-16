"""PaperlessPermissionReader and PaperlessPermissionWriter on responses recorded from 3.4.2."""

import json
from typing import TYPE_CHECKING

import httpx2
import pytest

from paperless_mcp.domain.bulk import ObjectPermissions
from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation
from paperless_mcp.domain.permissions import AccessChange
from paperless_mcp.paperless.permissions import PaperlessPermissionReader, PaperlessPermissionWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]


def tag_row(number: int, owner: int | None, permissions: Json) -> Json:
    """A tag as `?full_perms=true` shows it (no `user_can_change`; `permissions` instead)."""
    return {
        "id": number,
        "slug": f"plmcp28-t-{number}",
        "name": f"plmcp28-t-{number}",
        "color": "#a6cee3",
        "text_color": "#000000",
        "match": "",
        "matching_algorithm": 1,
        "is_insensitive": True,
        "is_inbox_tag": False,
        "document_count": 0,
        "owner": owner,
        "permissions": permissions,
        "parent": None,
        "children": [],
    }


def grants(
    view_users: tuple[int, ...] = (),
    view_groups: tuple[int, ...] = (),
    change_users: tuple[int, ...] = (),
    change_groups: tuple[int, ...] = (),
) -> Json:
    return {
        "view": {"users": list(view_users), "groups": list(view_groups)},
        "change": {"users": list(change_users), "groups": list(change_groups)},
    }


# A document row of `?fields=id,title,owner,permissions&full_perms=true`.
DOCUMENT: Json = {
    "id": 5354,
    "title": "plmcp28-d-c1b70e",
    "owner": 2,
    "permissions": grants((488,), (3,), (), (3,)),
}
USER: Json = {
    "id": 489,
    "username": "plmcp28-u2",
    "email": "",
    "password": "*****************************************************************************",
    "first_name": "Zed",
    "last_name": "Zoo",
    "date_joined": "2026-11-13T22:27:40.311496Z",
    "is_staff": False,
    "is_active": True,
    "is_superuser": False,
    "groups": [3],
    "user_permissions": [],
    "inherited_permissions": ["documents.view_document"],
    "is_mfa_enabled": False,
}


class Recorded:
    """Answers every request with one body and records method, path, query and JSON body."""

    def __init__(self, body: object, status: int = 200) -> None:
        self.body: object = body
        self.status: int = status
        self.sent: list[tuple[str, str, dict[str, str], object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload: object = json.loads(request.content) if request.content else None
        self.sent.append((request.method, request.url.path, dict(request.url.params), payload))
        return httpx2.Response(self.status, json=self.body)


def listing(*rows: Json, count: int | None = None, after: str | None = None) -> Json:
    link = f"http://paperless.test/api/x/?{after}" if after else None
    return {"count": len(rows) if count is None else count, "next": link, "previous": None,
            "results": list(rows)}  # fmt: skip


# --- get_permissions


async def test_the_grants_of_tags_are_read_in_one_request_and_hidden_ones_are_left_out(
    make_client: ClientFactory,
) -> None:
    rows = [
        tag_row(7, 2, grants((488,), (), (488,), ())),
        tag_row(9, None, grants()),
    ]
    server = Recorded(listing(*rows))

    found = await PaperlessPermissionReader(make_client(server)).get_permissions(
        "tag", [9, 8, 7, 9]
    )

    assert server.sent == [
        (
            "GET",
            "/api/tags/",
            {"id__in": "9,8,7", "full_perms": "true", "page_size": "3"},
            None,
        )
    ]
    assert [row.id for row in found] == [9, 7]
    assert found[0].owner is None
    assert found[0].permissions == ObjectPermissions()
    assert found[1].name == "plmcp28-t-7"
    assert found[1].permissions == ObjectPermissions(view_users=(488,), change_users=(488,))


async def test_documents_ask_for_the_fields_they_need_and_use_the_title_as_the_name(
    make_client: ClientFactory,
) -> None:
    server = Recorded(listing(DOCUMENT))

    [found] = await PaperlessPermissionReader(make_client(server)).get_permissions(
        "document", [5354]
    )

    assert server.sent[0][2] == {
        "id__in": "5354",
        "full_perms": "true",
        "page_size": "1",
        "fields": "id,title,owner,permissions",
    }
    assert (found.id, found.name, found.owner) == (5354, "plmcp28-d-c1b70e", 2)
    assert found.permissions == ObjectPermissions(
        view_users=(488,), view_groups=(3,), change_groups=(3,)
    )


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("correspondent", "/api/correspondents/"),
        ("document_type", "/api/document_types/"),
        ("storage_path", "/api/storage_paths/"),
    ],
)
async def test_every_taxonomy_kind_has_its_own_endpoint(
    make_client: ClientFactory, kind: str, path: str
) -> None:
    server = Recorded(listing())

    found = await PaperlessPermissionReader(make_client(server)).get_permissions(
        kind,  # pyright: ignore[reportArgumentType] -- the parameter is the Literal of kinds
        [1],
    )

    assert found == []
    assert server.sent[0][1] == path


async def test_no_ids_send_no_request(make_client: ClientFactory) -> None:
    server = Recorded(listing())

    found = await PaperlessPermissionReader(make_client(server)).get_permissions("tag", [])

    assert (found, server.sent) == ([], [])


async def test_a_forbidden_list_is_a_permission_error(make_client: ClientFactory) -> None:
    server = Recorded({"detail": "You do not have permission to perform this action."}, 403)

    with pytest.raises(PermissionDenied):
        _ = await PaperlessPermissionReader(make_client(server)).get_permissions("tag", [1])


# --- set_permissions


async def test_all_four_lists_are_sent_whole_and_the_owner_only_when_it_is_set(
    make_client: ClientFactory,
) -> None:
    server = Recorded(tag_row(7, 2, grants((488,), (3,), (488,), (3,))))
    writer = PaperlessPermissionWriter(make_client(server))

    changed = await writer.set_permissions(
        "tag",
        7,
        AccessChange(
            permissions=ObjectPermissions(
                view_users=(488,), view_groups=(3,), change_users=(488,), change_groups=(3,)
            )
        ),
    )

    assert server.sent == [
        (
            "PATCH",
            "/api/tags/7/",
            {"full_perms": "true"},
            {
                "set_permissions": {
                    "view": {"users": [488], "groups": [3]},
                    "change": {"users": [488], "groups": [3]},
                }
            },
        )
    ]
    assert changed.permissions == ObjectPermissions(
        view_users=(488,), view_groups=(3,), change_users=(488,), change_groups=(3,)
    )


async def test_an_owner_alone_is_sent_alone_and_none_removes_it(make_client: ClientFactory) -> None:
    server = Recorded(tag_row(7, None, grants()))
    writer = PaperlessPermissionWriter(make_client(server))

    handed = await writer.set_permissions("tag", 7, AccessChange(owner=489))
    cleared = await writer.set_permissions("tag", 7, AccessChange(owner=None))

    assert [sent[3] for sent in server.sent] == [{"owner": 489}, {"owner": None}]
    assert handed.owner is None  # whatever the recorded answer says: the port returns the body
    assert cleared.owner is None


async def test_an_empty_change_sends_an_empty_body(make_client: ClientFactory) -> None:
    server = Recorded(tag_row(7, 2, grants()))

    _ = await PaperlessPermissionWriter(make_client(server)).set_permissions(
        "tag", 7, AccessChange()
    )

    assert server.sent[0][3] == {}


async def test_documents_are_patched_on_their_own_endpoint(make_client: ClientFactory) -> None:
    server = Recorded({**DOCUMENT, "content": "text"})

    changed = await PaperlessPermissionWriter(make_client(server)).set_permissions(
        "document", 5354, AccessChange(owner=2)
    )

    # No `fields` here: with it Paperless ignores set_permissions on a document (probed on 3.4.2).
    assert server.sent[0][:3] == ("PATCH", "/api/documents/5354/", {"full_perms": "true"})
    assert (changed.id, changed.name) == (5354, "plmcp28-d-c1b70e")


async def test_a_forbidden_patch_says_who_may_change_permissions(
    make_client: ClientFactory,
) -> None:
    server = Recorded({"detail": "You do not have permission to perform this action."}, 403)

    with pytest.raises(PermissionDenied) as refusal:
        _ = await PaperlessPermissionWriter(make_client(server)).set_permissions(
            "document_type", 1, AccessChange(owner=2)
        )

    assert refusal.value.message == (
        "The Paperless user may not change the permissions of this document type."
    )
    assert refusal.value.hint == (
        "It needs the permission to change document types and must own the document type (or it "
        "must have no owner), or be a superuser."
    )


async def test_an_unknown_user_is_a_validation_error_and_a_hidden_object_is_not_found(
    make_client: ClientFactory,
) -> None:
    unknown = Recorded(
        {"set_permissions": ["Some users in don't exist or were specified twice."]}, 400
    )
    hidden = Recorded({"detail": "No Tag matches the given query."}, 404)
    change = AccessChange(permissions=ObjectPermissions(view_users=(99999,)))

    with pytest.raises(Validation, match="Some users"):
        _ = await PaperlessPermissionWriter(make_client(unknown)).set_permissions("tag", 7, change)
    with pytest.raises(NotFound):
        _ = await PaperlessPermissionWriter(make_client(hidden)).set_permissions("tag", 7, change)


# --- users and groups


async def test_users_are_listed_as_compact_rows_with_the_username_filter(
    make_client: ClientFactory,
) -> None:
    server = Recorded(
        listing(
            USER,
            {**USER, "id": 490, "username": "plmcp28-u3", "first_name": "", "last_name": ""},
            count=5,
            after="page=3&page_size=2",
        )
    )

    page = await PaperlessPermissionReader(make_client(server)).list_users(
        name_contains="plmcp28", page=2, page_size=2
    )

    assert server.sent == [
        (
            "GET",
            "/api/users/",
            {
                "page": "2",
                "page_size": "2",
                "username__icontains": "plmcp28",
            },
            None,
        )
    ]
    assert (page.count, page.next_page) == (5, 3)
    first, second = page.rows
    assert (first.id, first.username, first.name) == (489, "plmcp28-u2", "Zed Zoo")
    assert (first.is_active, first.is_superuser, first.group_ids) == (True, False, (3,))
    assert second.name is None
    assert "email" not in type(first).model_fields
    assert "password" not in type(first).model_fields


async def test_a_users_list_without_a_filter_sends_none_and_a_page_past_the_end_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recorded(listing())
    gone = Recorded({"detail": "Invalid page."}, 404)

    page = await PaperlessPermissionReader(make_client(server)).list_users(
        name_contains=None, page=1, page_size=25
    )
    with pytest.raises(NotFound, match="Page 9 of the users does not exist") as beyond:
        _ = await PaperlessPermissionReader(make_client(gone)).list_users(
            name_contains=None, page=9, page_size=25
        )
    assert beyond.value.hint == (
        "List the users with page=1; count and next_page tell how far it goes."
    )

    assert "username__icontains" not in server.sent[0][2]
    assert (page.count, page.next_page, page.rows) == (0, None, ())


async def test_groups_are_listed_by_name(make_client: ClientFactory) -> None:
    group: Json = {"id": 3, "name": "plmcp28-g1", "permissions": ["view_document"]}
    server = Recorded(listing(group, count=11, after="page=2&page_size=10"))
    gone = Recorded({"detail": "Invalid page."}, 404)

    page = await PaperlessPermissionReader(make_client(server)).list_groups(
        name_contains="g1", page=1, page_size=10
    )
    with pytest.raises(NotFound, match="Page 4 of the groups does not exist"):
        _ = await PaperlessPermissionReader(make_client(gone)).list_groups(
            name_contains=None, page=4, page_size=10
        )

    assert server.sent[0][2] == {
        "page": "1",
        "page_size": "10",
        "name__icontains": "g1",
    }
    assert [(row.id, row.name) for row in page.rows] == [(3, "plmcp28-g1")]
    assert (page.count, page.next_page) == (11, 2)


async def test_listing_users_or_groups_without_the_permission_is_a_permission_error(
    make_client: ClientFactory,
) -> None:
    server = Recorded({"detail": "You do not have permission to perform this action."}, 403)
    reader = PaperlessPermissionReader(make_client(server))

    with pytest.raises(PermissionDenied):
        _ = await reader.list_users(name_contains=None, page=1, page_size=25)
    with pytest.raises(PermissionDenied):
        _ = await reader.list_groups(name_contains=None, page=1, page_size=25)
