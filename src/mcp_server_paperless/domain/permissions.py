"""Permissions: who owns an object and who may view or change it, and the users and groups.

Documents, tags, correspondents, document types, storage paths, mail accounts, mail rules and
saved views each have an owner (null: no owner, every user with the model's permission may use the
object) and grants for users and groups. A grant to change includes the grant to view: Paperless
stores both, and a change grant always comes with a view grant when it is set. The port reads and
writes ids; the tools speak names, and the results carry both.

Output models are tool output schemas: a one-line class docstring each, and field descriptions
only where the name does not say enough.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.documents import NameOrId

# Most objects one set_permissions call may name: a page of search_documents results (100) fits.
MAX_OBJECTS = 100

type SharedKind = Literal["tag", "correspondent", "document_type", "storage_path", "saved_view"]
# One flat list, so that the schema of the tool is one enum.
type PermissionKind = Literal[
    "document",
    "tag",
    "correspondent",
    "document_type",
    "storage_path",
    "mail_account",
    "mail_rule",
    "saved_view",
]
type Mode = Literal["merge", "replace"]


class PermissionsRequest(BaseModel):
    """What a caller asks set_permissions to do to every selected object (names or ids).

    Lists that are left out (null) are not touched in either mode; a list that is given and
    empty clears (replace) or adds nothing (merge).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    owner: Annotated[NameOrId | None, Field(description="Username or id of the new owner.")] = None
    clear_owner: Annotated[
        bool, Field(description="Remove the owner: every user may then use the objects.")
    ] = False
    view_users: Annotated[
        list[NameOrId] | None, Field(description="Users who may view (usernames or ids).")
    ] = None
    view_groups: Annotated[
        list[NameOrId] | None, Field(description="Groups who may view (names or ids).")
    ] = None
    change_users: Annotated[
        list[NameOrId] | None,
        Field(description="Users who may change, and so view (usernames or ids)."),
    ] = None
    change_groups: Annotated[
        list[NameOrId] | None,
        Field(description="Groups who may change, and so view (names or ids)."),
    ] = None
    mode: Mode = "merge"


class ObjectAccess(BaseModel):
    """What the port reads of one object: its name or title, owner and grants (ids)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    owner: int | None
    permissions: ObjectPermissions


class AccessChange(BaseModel):
    """What the port writes to one object: the whole new grants and/or the owner.

    ``permissions`` is sent as it is: lists that are empty remove the grants of that list, so
    callers compute the complete result first. ``owner`` is sent only when it was set (``None``
    removes the owner).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    owner: int | None = None
    permissions: ObjectPermissions | None = None


class UserRow(BaseModel):
    """A Paperless user as a compact row (no e-mail address, no permission lists)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    username: str
    name: Annotated[str | None, Field(description="First and last name, if the user has them.")]
    is_active: bool
    is_superuser: Annotated[
        bool, Field(description="Superusers see and change everything, whatever is granted.")
    ]
    group_ids: Annotated[tuple[int, ...], Field(description="Ids of the groups the user is in.")]


class GroupRow(BaseModel):
    """A Paperless user group as a compact row (no permission list)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str


class UserPage(BaseModel):
    """One page of users as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    rows: tuple[UserRow, ...]


class GroupPage(BaseModel):
    """One page of groups as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    rows: tuple[GroupRow, ...]


class UserList(BaseModel):
    """One page of users."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many users match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    users: tuple[UserRow, ...]


class GroupList(BaseModel):
    """One page of groups."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many groups match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    groups: tuple[GroupRow, ...]


# What comes back from set_permissions.


class Principal(BaseModel):
    """A user or group by id and name (``#id`` if the caller may not list them)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str


class AccessView(BaseModel):
    """Who owns an object and who may view or change it, by name."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    owner: Annotated[Principal | None, Field(description="Null: no owner, every user may use it.")]
    view_users: Annotated[
        tuple[Principal, ...], Field(description="Users who may view it but not change it.")
    ]
    view_groups: Annotated[
        tuple[Principal, ...], Field(description="Groups who may view it but not change it.")
    ]
    change_users: Annotated[
        tuple[Principal, ...], Field(description="Users who may view and change it.")
    ]
    change_groups: Annotated[
        tuple[Principal, ...], Field(description="Groups who may view and change it.")
    ]


class ObjectResult(BaseModel):
    """One object with its access after the call."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: Annotated[str, Field(description="The title of a document, else the name.")]
    web_url: Annotated[
        str | None, Field(description="Documents: link in the Paperless web UI; else null.")
    ]
    changed: Annotated[bool, Field(description="false: it already was as asked.")]
    access: AccessView


class PermissionsResult(BaseModel):
    """What set_permissions did, object by object."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: PermissionKind
    mode: Mode
    changed_count: int
    unchanged_count: Annotated[
        int, Field(description="Objects that already were as asked; they were left alone.")
    ]
    objects: tuple[ObjectResult, ...]
    message: str
