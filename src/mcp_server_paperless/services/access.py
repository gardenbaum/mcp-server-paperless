"""Who owns an object and who may use it, as tools show it: by name."""

from typing import TYPE_CHECKING

from mcp_server_paperless.domain.permissions import AccessView, Principal
from mcp_server_paperless.services.principals import names_of

if TYPE_CHECKING:
    from mcp_server_paperless.domain.bulk import ObjectPermissions
    from mcp_server_paperless.domain.permissions import ObjectAccess, PermissionKind
    from mcp_server_paperless.domain.ports import PermissionReader
    from mcp_server_paperless.services.principals import PrincipalKind
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver


def users_of(grants: ObjectPermissions) -> set[int]:
    """Every user the grants name."""
    return {*grants.view_users, *grants.change_users}


def groups_of(grants: ObjectPermissions) -> set[int]:
    """Every group the grants name."""
    return {*grants.view_groups, *grants.change_groups}


def owner_ids(access: ObjectAccess) -> set[int]:
    """The owner, if there is one."""
    return set() if access.owner is None else {access.owner}


def _principal(number: int, names: dict[int, str]) -> Principal:
    return Principal(id=number, name=names[number])


def access_view(access: ObjectAccess, names: dict[PrincipalKind, dict[int, str]]) -> AccessView:
    """The access in names; view lists hold those who may only view."""
    grants = access.permissions
    users, groups = names["user"], names["group"]
    return AccessView(
        owner=None if access.owner is None else _principal(access.owner, users),
        view_users=tuple(
            _principal(n, users) for n in sorted(set(grants.view_users) - set(grants.change_users))
        ),
        view_groups=tuple(
            _principal(n, groups)
            for n in sorted(set(grants.view_groups) - set(grants.change_groups))
        ),
        change_users=tuple(_principal(n, users) for n in sorted(grants.change_users)),
        change_groups=tuple(_principal(n, groups) for n in sorted(grants.change_groups)),
    )


async def read_access(
    reader: PermissionReader, taxonomy: TaxonomyResolver, kind: PermissionKind, number: int
) -> AccessView | None:
    """Who owns an object and who may use it, by name; ``None`` where the user sees no such object.

    Raises:
        PermissionDenied: The user may not view the kind.
    """
    found = await reader.get_permissions(kind, [number])
    if not found:
        return None
    [access] = found
    users = users_of(access.permissions) | owner_ids(access)
    groups = groups_of(access.permissions)
    names: dict[PrincipalKind, dict[int, str]] = {
        "user": await names_of(taxonomy, "user", sorted(users)) if users else {},
        "group": await names_of(taxonomy, "group", sorted(groups)) if groups else {},
    }
    return access_view(access, names)
