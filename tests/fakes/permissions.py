"""What the fake knows about permissions: reading and applying owners and grants.

Probed on Paperless 3.4.2 (PLMCP-28): a PATCH with ``set_permissions`` replaces, per action
(``view``, ``change``) and per kind (``users``, ``groups``), the lists it carries and keeps the
ones it leaves out; granting change also grants view; an unknown user or group is a 400 for the
whole request.
"""

from typing import TYPE_CHECKING, Protocol

from fakes.document_values import reject
from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.permissions import ObjectAccess

if TYPE_CHECKING:
    from collections.abc import Collection


class Granted(Protocol):
    """What the helpers need of a fake document or taxonomy object (`Owned` has all of it)."""

    id: int
    owner: int | None
    viewers: set[int]
    editors: set[int]
    view_groups: set[int]
    change_groups: set[int]


class Identified(Protocol):
    """An id, for the users and groups the grants refer to."""

    @property
    def id(self) -> int: ...


def access_of(obj: Granted, name: str) -> ObjectAccess:
    """The object as ``full_perms=true`` shows it: change grants are listed with view."""
    return ObjectAccess(
        id=obj.id,
        name=name,
        owner=obj.owner,
        permissions=ObjectPermissions(
            view_users=tuple(sorted(obj.viewers | obj.editors)),
            view_groups=tuple(sorted(obj.view_groups | obj.change_groups)),
            change_users=tuple(sorted(obj.editors)),
            change_groups=tuple(sorted(obj.change_groups)),
        ),
    )


def check_principals(
    given: ObjectPermissions, users: Collection[Identified], groups: Collection[Identified]
) -> None:
    """Reject unknown users and groups like Paperless: a 400 naming the field."""
    known_users, known_groups = {u.id for u in users}, {g.id for g in groups}
    if not {*given.view_users, *given.change_users} <= known_users:
        reject("set_permissions", "Some users in don't exist or were specified twice.")
    if not {*given.view_groups, *given.change_groups} <= known_groups:
        reject("set_permissions", "Some groups in don't exist or were specified twice.")


def set_grants(obj: Granted, given: ObjectPermissions) -> None:
    """Replace the lists of ``obj`` with ``given``; change grants bring view grants along."""
    obj.viewers = {*given.view_users, *given.change_users}
    obj.editors = set(given.change_users)
    obj.view_groups = {*given.view_groups, *given.change_groups}
    obj.change_groups = set(given.change_groups)
