"""PermissionReader and PermissionWriter on the object endpoints and the user and group lists.

Documents, tags, correspondents, document types and storage paths take ``owner`` and
``set_permissions`` on PATCH, and show their grants when the request carries ``full_perms=true``
(without it only ``user_can_change``). Probed on 3.4.2 (PLMCP-28):

- A PATCH with ``set_permissions`` replaces, per action (``view``, ``change``) and per kind
  (``users``, ``groups``), the lists it carries and keeps the ones it leaves out. An empty list
  removes every grant of that list. This adapter always sends all four lists, so it never relies
  on what an omitted list does.
- Granting change adds the view grant of the same user or group, but replacing the view list
  does not bring back a view grant a change grant had: callers send view lists that include
  every change grant.
- An unknown user or group is a 400 for the whole request; so is an unknown action.
- ``id__in`` with ``full_perms=true`` lists the grants of many objects in one request; objects
  the user may not see are not listed. Documents take ``fields`` to leave out their text on the
  list, but NOT on the PATCH: with ``fields`` a document PATCH answers 200 and silently ignores
  ``set_permissions`` (probed: the grants stay as they were), so the answer of a document PATCH
  carries its whole text and the adapter reads only the fields it needs.
- Mail accounts and rules (3.5.1, PLMCP-81.5) answer their grants with ``full_perms=true`` as well,
  though the schema does not say so; nothing but owner and grants is sent, so the saved password
  stays, and the mask of it that Paperless answers is not read.
- Saved views (3.5.1, PLMCP-88.1) ignore ``id__in`` (the list answers its first rows), and set the
  grants before they refuse an owner who has a view of that name.
- The users and groups lists are sent in Paperless's own order (case-insensitive by name).
"""

from typing import TYPE_CHECKING, Final

from pydantic import AliasChoices, BaseModel, Field

from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied
from mcp_server_paperless.domain.permissions import (
    GroupPage,
    GroupRow,
    ObjectAccess,
    UserPage,
    UserRow,
)
from mcp_server_paperless.paperless.pagination import Page

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.permissions import AccessChange, PermissionKind
    from mcp_server_paperless.paperless.client import PaperlessClient

PATHS: Final[dict[PermissionKind, str]] = {
    "document": "/api/documents/",
    "tag": "/api/tags/",
    "correspondent": "/api/correspondents/",
    "document_type": "/api/document_types/",
    "storage_path": "/api/storage_paths/",
    "mail_account": "/api/mail_accounts/",
    "mail_rule": "/api/mail_rules/",
    "saved_view": "/api/saved_views/",
}
# Their lists have no `id__in`: the objects are read one by one.
ONE_BY_ONE: Final[frozenset[PermissionKind]] = frozenset(
    {"mail_account", "mail_rule", "saved_view"}
)
USERS = "/api/users/"
GROUPS = "/api/groups/"
USERS_LABEL = "users"
GROUPS_LABEL = "groups"
# What a document list row needs: without this Paperless sends the whole text of every document.
DOCUMENT_FIELDS = "id,title,owner,permissions"


class _Grant(BaseModel):
    users: list[int]
    groups: list[int]


class _Permissions(BaseModel):
    view: _Grant
    change: _Grant


class _Row(BaseModel):
    id: int
    name: str = Field(validation_alias=AliasChoices("name", "title"))
    owner: int | None
    permissions: _Permissions


class _User(BaseModel):
    id: int
    username: str
    first_name: str = ""
    last_name: str = ""
    is_active: bool
    is_superuser: bool
    groups: list[int]


class _Group(BaseModel):
    id: int
    name: str


def _access(row: _Row) -> ObjectAccess:
    given = row.permissions
    return ObjectAccess(
        id=row.id,
        name=row.name,
        owner=row.owner,
        permissions=ObjectPermissions(
            view_users=tuple(given.view.users),
            view_groups=tuple(given.view.groups),
            change_users=tuple(given.change.users),
            change_groups=tuple(given.change.groups),
        ),
    )


def refused(kind: PermissionKind) -> PermissionDenied:
    """The error for a 403 of a PATCH: the permission or the ownership is missing."""
    label = kind.replace("_", " ")
    return PermissionDenied(
        f"The Paperless user may not change the permissions of this {label}.",
        hint=(
            f"It needs the permission to change {label}s and must own the {label} (or it must "
            "have no owner), or be a superuser."
        ),
    )


def beyond_last_page(what: str, page: int) -> NotFound:
    """The error for a page past the end of the users or groups."""
    return NotFound(
        f"Page {page} of the {what} does not exist.",
        hint=f"List the {what} with page=1; count and next_page tell how far it goes.",
    )


def _body(change: AccessChange) -> dict[str, JsonValue]:
    """The PATCH body: the owner only if the change sets one, the four lists whole."""
    body: dict[str, JsonValue] = {}
    if "owner" in change.model_fields_set:
        body["owner"] = change.owner
    if change.permissions is not None:
        grants = change.permissions
        body["set_permissions"] = {
            "view": {"users": list(grants.view_users), "groups": list(grants.view_groups)},
            "change": {"users": list(grants.change_users), "groups": list(grants.change_groups)},
        }
    return body


class PaperlessPermissionReader:
    """``PermissionReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def get_permissions(
        self, kind: PermissionKind, object_ids: Sequence[int]
    ) -> list[ObjectAccess]:
        """See ``PermissionReader.get_permissions``."""
        wanted = list(dict.fromkeys(object_ids))
        if not wanted:
            return []
        if kind in ONE_BY_ONE:
            return [row for number in wanted if (row := await self._one(kind, number)) is not None]
        params: dict[str, str | int] = {
            "id__in": ",".join(map(str, wanted)),
            "full_perms": "true",
            "page_size": len(wanted),
        }
        if kind == "document":
            params["fields"] = DOCUMENT_FIELDS
        found = await self._client.get(PATHS[kind], Page[_Row], params=params)
        rows = {row.id: row for row in found.results}
        return [_access(rows[number]) for number in wanted if number in rows]

    async def _one(self, kind: PermissionKind, number: int) -> ObjectAccess | None:
        path = f"{PATHS[kind]}{number}/"
        try:
            return _access(await self._client.get(path, _Row, params={"full_perms": "true"}))
        except NotFound:
            return None

    async def list_users(self, *, name_contains: str | None, page: int, page_size: int) -> UserPage:
        """See ``PermissionReader.list_users``."""
        params: dict[str, str | int] = {
            "page": page,
            "page_size": page_size,
        }
        if name_contains:
            params["username__icontains"] = name_contains
        try:
            found = await self._client.get(USERS, Page[_User], params=params)
        except NotFound:
            raise beyond_last_page(USERS_LABEL, page) from None
        rows = tuple(
            UserRow(
                id=row.id,
                username=row.username,
                name=f"{row.first_name} {row.last_name}".strip() or None,
                is_active=row.is_active,
                is_superuser=row.is_superuser,
                group_ids=tuple(row.groups),
            )
            for row in found.results
        )
        return UserPage(count=found.count, next_page=found.next_page, rows=rows)

    async def list_groups(
        self, *, name_contains: str | None, page: int, page_size: int
    ) -> GroupPage:
        """See ``PermissionReader.list_groups``."""
        params: dict[str, str | int] = {
            "page": page,
            "page_size": page_size,
        }
        if name_contains:
            params["name__icontains"] = name_contains
        try:
            found = await self._client.get(GROUPS, Page[_Group], params=params)
        except NotFound:
            raise beyond_last_page(GROUPS_LABEL, page) from None
        rows = tuple(GroupRow(id=row.id, name=row.name) for row in found.results)
        return GroupPage(count=found.count, next_page=found.next_page, rows=rows)


class PaperlessPermissionWriter:
    """``PermissionWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def set_permissions(
        self, kind: PermissionKind, object_id: int, change: AccessChange
    ) -> ObjectAccess:
        """See ``PermissionWriter.set_permissions``."""
        path = f"{PATHS[kind]}{object_id}/"
        try:
            row = await self._client.call(
                "PATCH", path, _Row, params={"full_perms": "true"}, json=_body(change)
            )
        except PermissionDenied:
            raise refused(kind) from None
        return _access(row)
