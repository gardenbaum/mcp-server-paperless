"""Users and groups named by callers: resolved to ids and back to names for messages.

Shared by every service that grants access (bulk_edit_taxonomy, set_permissions). A name or id
is checked against the list the user may see (decision-5). A user without the permission to list
users or groups cannot have a name resolved, but may pass ids: Paperless then decides, and
refuses unknown ones before it changes anything.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from mcp_server_paperless.domain.errors import PermissionDenied, quoted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.documents import NameOrId
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

type PrincipalKind = Literal["user", "group"]


@dataclass(frozen=True, slots=True)
class Principals:
    """Users or groups as ids, with the words that name them in a message."""

    ids: tuple[int, ...]
    names: tuple[str, ...]


def is_numeric(value: NameOrId) -> bool:
    """Whether ``value`` is an id: an int, or a string of ASCII digits."""
    return isinstance(value, int) or (value.strip().isascii() and value.strip().isdigit())


async def resolve_principals(
    resolver: TaxonomyResolver, kind: PrincipalKind, values: Sequence[NameOrId]
) -> Principals:
    """Ids and quoted names of users or groups, checked against the list the user may see.

    An id is passed on unchecked only when the user may not list that kind.

    Raises:
        NotFound: A user or group matches nothing.
        Ambiguous: A name matches more than one.
        PermissionDenied: The user may not list ``kind`` and gave a name.
    """
    ids: list[int] = []
    names: list[str] = []
    for value in values:
        try:
            found = await resolver.resolve(kind, value)
        except PermissionDenied as error:
            if not is_numeric(value):
                hint = f"Pass the {kind} id instead of the name."
                raise PermissionDenied(error.message, hint=hint) from None
            ids.append(int(value))  # the user may not list them: Paperless decides
            names.append(f"#{int(value)}")
            continue
        ids.append(found.id)
        names.append(quoted(found.name))
    return Principals(ids=tuple(ids), names=tuple(names))


async def names_of(
    resolver: TaxonomyResolver, kind: PrincipalKind, ids: Sequence[int]
) -> dict[int, str]:
    """The names of users or groups by id; ``#id`` for those the user may not list or see."""
    try:
        known = {row.id: row.name for row in await resolver.items(kind)}
    except PermissionDenied:
        known = {}
    return {number: known.get(number, f"#{number}") for number in ids}
