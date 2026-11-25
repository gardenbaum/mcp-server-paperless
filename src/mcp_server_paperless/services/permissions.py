"""Owners and grants of documents, taxonomy and mail objects and saved views, and their users.

``set_permissions`` reads the current owner and grants of every selected object, works out the
complete new grants and sends only the objects that change. Two modes (the lists a caller leaves
out, null, are never touched):

- ``merge`` adds the given users and groups to the lists.
- ``replace`` makes each given list exactly what was asked, an empty list included.

Granting access is exposure that cannot be taken back, so every call that changes anything needs
``confirm`` in both modes and the refusal names who gains and who loses access (decision-19,
decision-15); changing the owner, also to none, is covered by it (decision-12). A call that
changes nothing is neither sent nor confirmed.

A change grant includes the view grant: a user who may change always may view, so replacing a
view list never takes view from a changer; replace the change list for that. Unknown users and
groups fail before anything is sent. The objects are sent one at a time; if one fails, the error
says which were already changed.
"""

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamUnavailable,
    Validation,
    listed,
    quoted,
)
from mcp_server_paperless.domain.permissions import (
    MAX_OBJECTS,
    AccessChange,
    GroupList,
    ObjectResult,
    PermissionsResult,
    UserList,
)
from mcp_server_paperless.services.access import access_view, groups_of, owner_ids, users_of
from mcp_server_paperless.services.document_runner import OUT_OF_TIME, RUN_SECONDS
from mcp_server_paperless.services.documents import document_link
from mcp_server_paperless.services.principals import is_numeric, names_of, resolve_principals

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mcp_server_paperless.domain.documents import NameOrId
    from mcp_server_paperless.domain.permissions import (
        Mode,
        ObjectAccess,
        PermissionKind,
        PermissionsRequest,
        SharedKind,
    )
    from mcp_server_paperless.domain.ports import InstanceReader, PermissionReader, PermissionWriter
    from mcp_server_paperless.services.principals import PrincipalKind
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# Objects whose details a confirmation names; the others are counted.
SHOWN: Final = 5
# The taxonomy kinds whose document counts depend on who may see a document.
COUNTED: Final[tuple[SharedKind, ...]] = (
    "tag",
    "correspondent",
    "document_type",
    "storage_path",
)
# The kinds that are chosen by name or id and cached.
SHARED: Final[dict[PermissionKind, SharedKind]] = {
    **{kind: kind for kind in COUNTED},
    "saved_view": "saved_view",
}
LABELS: Final[dict[PermissionKind, str]] = {
    "document": "document",
    "tag": "tag",
    "correspondent": "correspondent",
    "document_type": "document type",
    "storage_path": "storage path",
    "mail_account": "mail account",
    "mail_rule": "mail rule",
    "saved_view": "saved view",
}
# Where the ids of objects that are chosen by id only are found.
FINDERS: Final[dict[PermissionKind, str]] = {
    "document": "search_documents",
    "mail_account": "list_mail_accounts",
    "mail_rule": "list_mail_rules",
}
# What a grant to change, or another owner, gives beyond the object itself.
NOTES: Final[dict[PermissionKind, str]] = {
    "mail_account": (
        "Whoever may change a mail account can make Paperless send its saved password or token to "
        "a server of their choice, at once and without storing anything, and can add rules that "
        "import, move or delete its mail. A grant on an account is none on its rules"
    ),
    "mail_rule": (
        "Whoever may change a mail rule can make it move or delete mail with the saved password of "
        "its account, without any right on the account. The owner of a rule owns the mails it "
        "imports whole from then on, and attachments where assign_owner_from_rule is on; without "
        "an owner every user with the global permission to view documents sees them"
    ),
}
# The kinds whose objects are read one by one, so that a call is bounded in time.
TIMED: Final[frozenset[PermissionKind]] = frozenset({*NOTES, "saved_view"})
FEWER: Final = "Choose fewer objects per call."
NONE_GIVEN = "Nothing to change: no owner and no grants were given."
NONE_GIVEN_HINT = (
    "Pass owner, clear_owner, or users or groups in view_users, view_groups, change_users or "
    "change_groups (with mode replace an empty list removes that list's grants)."
)
NO_PRINCIPALS = "Nothing to change: every list is empty and merge only adds."
LISTING_HINT = (
    "Listing {} needs the view permission for them in Paperless. Ask an administrator for the "
    "ids; set_permissions takes ids too."
)
UNKNOWN_STATE = (
    "Whether the change to {} was applied is unknown, because the request is not retried; read "
    "its permissions before trying again. The objects after it were not changed."
)
# The owner a request asks for: one-tuple of the user (None removes the owner); None leaves it.
type _Owner = tuple[int | None]
# Levels of access, in order: none, view, change.
NONE, VIEW, CHANGE = 0, 1, 2


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


@dataclass(frozen=True, slots=True)
class _Asked:
    """The four lists of a request as ids; ``None`` is a list that was left out."""

    view_users: frozenset[int] | None
    view_groups: frozenset[int] | None
    change_users: frozenset[int] | None
    change_groups: frozenset[int] | None


@dataclass(frozen=True, slots=True)
class _Plan:
    """What happens to one object: its current state, the new grants and the new owner."""

    current: ObjectAccess
    grants: ObjectPermissions
    owner: int | None
    owner_changes: bool

    @property
    def grants_change(self) -> bool:
        """Whether the lists differ from the object's now."""
        return _normalised(self.grants) != _normalised(self.current.permissions)

    @property
    def scope(self) -> object:
        """The object with its access now and then: what a confirmation is about."""
        before, after = _normalised(self.current.permissions), _normalised(self.grants)
        return (self.current.id, self.current.owner, before, self.owner, after)

    @property
    def changes(self) -> bool:
        """Whether anything is sent for the object."""
        return self.owner_changes or self.grants_change


def _normalised(grants: ObjectPermissions) -> tuple[tuple[int, ...], ...]:
    """The lists sorted, with every change grant also in the view list."""
    return (
        tuple(sorted({*grants.view_users, *grants.change_users})),
        tuple(sorted({*grants.view_groups, *grants.change_groups})),
        tuple(sorted(grants.change_users)),
        tuple(sorted(grants.change_groups)),
    )


def _combined(
    viewers: set[int],
    changers: set[int],
    view: frozenset[int] | None,
    change: frozenset[int] | None,
    mode: Mode,
) -> tuple[set[int], set[int]]:
    """The new view and change sets of users (or groups): the rules in the module docstring."""
    if mode == "merge":
        added: set[int] = set(change or ())
        changers = changers | added
        return viewers | set(view or ()) | changers, changers
    changers = set(change) if change is not None else changers
    return (set(view) if view is not None else viewers) | changers, changers


def _new_grants(current: ObjectPermissions, asked: _Asked, mode: Mode) -> ObjectPermissions:
    """The complete lists after the request; lists left out stay."""
    users_view, users_change = _combined(
        {*current.view_users, *current.change_users},
        set(current.change_users),
        asked.view_users,
        asked.change_users,
        mode,
    )
    groups_view, groups_change = _combined(
        {*current.view_groups, *current.change_groups},
        set(current.change_groups),
        asked.view_groups,
        asked.change_groups,
        mode,
    )
    return ObjectPermissions(
        view_users=tuple(sorted(users_view)),
        view_groups=tuple(sorted(groups_view)),
        change_users=tuple(sorted(users_change)),
        change_groups=tuple(sorted(groups_change)),
    )


def _levels(viewers: Sequence[int], changers: Sequence[int]) -> dict[int, int]:
    """Each user's (or group's) level of access: view, or change which includes view."""
    return {number: CHANGE if number in changers else VIEW for number in {*viewers, *changers}}


class PermissionService:
    """Changes who owns objects and who may view or change them; lists users and groups."""

    def __init__(  # noqa: PLR0913 -- the ports it uses
        self,
        reader: PermissionReader,
        writer: PermissionWriter,
        taxonomy: TaxonomyResolver,
        instance: InstanceReader,
        *,
        web_url: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Read through ``reader``, change through ``writer``.

        ``instance`` tells who the caller is, so a refusal does not warn the caller about
        handing an object to themselves.

        ``taxonomy`` resolves names of taxonomy objects, users and groups and is the cache every
        write invalidates; ``web_url`` is the Paperless web UI, for links to documents.
        """
        self._reader: PermissionReader = reader
        self._writer: PermissionWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy
        self._instance: InstanceReader = instance
        self._web_url: str = web_url
        self._clock: Callable[[], float] = clock

    async def list_users(self, *, name_contains: str | None, page: int, page_size: int) -> UserList:
        """One page of users, optionally those whose username contains ``name_contains``.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user may not view users.
        """
        try:
            found = await self._reader.list_users(
                name_contains=name_contains, page=page, page_size=page_size
            )
        except PermissionDenied as error:
            raise PermissionDenied(error.message, hint=LISTING_HINT.format("users")) from None
        return UserList(count=found.count, page=page, next_page=found.next_page, users=found.rows)

    async def list_groups(
        self, *, name_contains: str | None, page: int, page_size: int
    ) -> GroupList:
        """One page of groups, optionally those whose name contains ``name_contains``.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user may not view groups.
        """
        try:
            found = await self._reader.list_groups(
                name_contains=name_contains, page=page, page_size=page_size
            )
        except PermissionDenied as error:
            raise PermissionDenied(error.message, hint=LISTING_HINT.format("groups")) from None
        return GroupList(count=found.count, page=page, next_page=found.next_page, groups=found.rows)

    async def set_permissions(
        self,
        kind: PermissionKind,
        items: Sequence[NameOrId],
        request: PermissionsRequest,
        *,
        confirm: bool = False,
    ) -> PermissionsResult:
        """Change the owner and/or the grants of the objects ``items`` name.

        Raises:
            Validation: The selection is empty or over ``MAX_OBJECTS``, documents are named
                instead of numbered, the request is empty or names owner and clear_owner, or
                Paperless rejects an id.
            NotFound: An object, user or group matches nothing, or the user may not see it.
            Ambiguous: A name matches more than one.
            ConfirmationRequired: Replacing grants or changing an owner without ``confirm``.
                Nothing was changed.
            PermissionDenied: The user may not change one of the objects, or may not list users
                or groups to resolve a name. If objects before it were changed, the message
                says so.
        """
        until = self._clock() + RUN_SECONDS if kind in TIMED else math.inf
        owner, asked = await self._asked(request)
        ids = await self._select(kind, items)
        found = await self._read(kind, ids, until)
        if len(found) != len(ids):
            raise _unseen(kind, [n for n in ids if n not in {row.id for row in found}])
        plans = [_plan(row, asked, owner, request.mode) for row in found]
        changing = [plan for plan in plans if plan.changes]
        names = await self._names(plans, [plan.grants for plan in changing])
        if changing and not confirm:
            caller = await self._caller()
            ConfirmationRequired.require(
                self._effect(kind, request.mode, changing, names, caller),
                scope=[plan.scope for plan in changing],
            )
        written = await self._write(kind, changing, until)
        return self._result(kind, request.mode, plans, written, names)

    async def _read(
        self, kind: PermissionKind, ids: Sequence[int], until: float
    ) -> list[ObjectAccess]:
        """The objects with their access; those of the ``TIMED`` kinds one by one.

        Raises:
            PaperlessMcpError: The time is over; nothing was changed.
        """
        if kind not in TIMED:
            return await self._reader.get_permissions(kind, ids)
        found: list[ObjectAccess] = []
        for number in ids:
            if self._clock() >= until:
                message = (
                    f"The objects could not be read within {RUN_SECONDS:g} seconds. Nothing was "
                    "changed."
                )
                raise PaperlessMcpError(message, hint=FEWER)
            found += await self._reader.get_permissions(kind, [number])
        return found

    async def _caller(self) -> int | None:
        """The id of the Paperless user of the call; None if Paperless does not say."""
        try:
            current = (await self._instance.get_overview()).current_user
        except PaperlessMcpError:
            return None
        return None if current is None else current.id

    async def _asked(self, request: PermissionsRequest) -> tuple[_Owner | None, _Asked]:
        """The owner and the lists of the request as ids; unknown users and groups fail here."""
        if request.owner is not None and request.clear_owner:
            message = "Give owner or clear_owner, not both."
            raise Validation(message, hint="clear_owner=true removes the owner.")
        lists: tuple[tuple[PrincipalKind, list[NameOrId] | None], ...] = (
            ("user", request.view_users),
            ("group", request.view_groups),
            ("user", request.change_users),
            ("group", request.change_groups),
        )
        owner_given = request.owner is not None or request.clear_owner
        if not owner_given and all(values is None for _, values in lists):
            raise Validation(NONE_GIVEN, hint=NONE_GIVEN_HINT)
        if not owner_given and request.mode == "merge" and not any(values for _, values in lists):
            raise Validation(NO_PRINCIPALS, hint=NONE_GIVEN_HINT)
        owner: _Owner | None = None
        if request.owner is not None:
            who = await resolve_principals(self._taxonomy, "user", [request.owner])
            owner = (who.ids[0],)
        if request.clear_owner:
            owner = (None,)
        resolved: list[frozenset[int] | None] = []
        for kind, values in lists:
            if values is None:
                resolved.append(None)
            else:
                who = await resolve_principals(self._taxonomy, kind, values)
                resolved.append(frozenset(who.ids))
        return owner, _Asked(*resolved)

    async def _select(self, kind: PermissionKind, items: Sequence[NameOrId]) -> list[int]:
        """The ids of the objects ``items`` name, each once, in the order given."""
        if not 1 <= len(items) <= MAX_OBJECTS:
            message = f"Select between 1 and {MAX_OBJECTS} objects per call."
            raise Validation(message, hint="Work in several steps.")
        shared = SHARED.get(kind)
        if shared is not None:
            return list(
                dict.fromkeys([(await self._taxonomy.resolve(shared, item)).id for item in items])
            )
        if not all(is_numeric(item) for item in items):
            label = LABELS[kind].capitalize()
            by = "title" if kind == "document" else "name"
            message = f"{label}s are selected by id, not by {by}."
            raise Validation(message, hint=f"Find the ids with {FINDERS[kind]}.")
        return list(dict.fromkeys(int(item) for item in items))

    async def _names(
        self, plans: Sequence[_Plan], new: Sequence[ObjectPermissions]
    ) -> dict[PrincipalKind, dict[int, str]]:
        """The names of every user and group the call mentions, by id."""
        users: set[int] = set()
        groups: set[int] = set()
        for plan in plans:
            users |= users_of(plan.current.permissions) | owner_ids(plan.current)
            groups |= groups_of(plan.current.permissions)
            if plan.owner_changes and plan.owner is not None:
                users.add(plan.owner)
        for grants in new:
            users |= users_of(grants)
            groups |= groups_of(grants)
        return {
            "user": await names_of(self._taxonomy, "user", sorted(users)),
            "group": await names_of(self._taxonomy, "group", sorted(groups)),
        }

    async def _write(
        self, kind: PermissionKind, plans: Sequence[_Plan], until: float
    ) -> dict[int, ObjectAccess]:
        """Send the plans one at a time; forget what the resolver cached whatever happened."""
        written: dict[int, ObjectAccess] = {}
        try:
            for plan in plans:
                if self._clock() >= until:
                    late = PaperlessMcpError(OUT_OF_TIME, hint=FEWER)
                    raise _rebuilt(late, _halfway(late, plan, plans, written, unknown=False))
                try:
                    written[plan.current.id] = await self._writer.set_permissions(
                        kind, plan.current.id, _change(plan)
                    )
                except PaperlessMcpError as error:
                    unknown = (
                        isinstance(error, UpstreamUnavailable) or type(error) is PaperlessMcpError
                    )
                    grants = await self._grants_kept(kind, plan, error)
                    if not written and not unknown and not grants:
                        raise
                    message = _halfway(error, plan, plans, written, unknown=unknown, grants=grants)
                    raise _rebuilt(error, message) from None
        finally:
            for kind_to_forget in _cached(kind):
                self._taxonomy.invalidate(kind_to_forget)
        return written

    async def _grants_kept(
        self, kind: PermissionKind, plan: _Plan, error: PaperlessMcpError
    ) -> str:
        """What became of the grants that were sent with an owner Paperless refused; mostly empty.

        Paperless can set the grants before it refuses the owner, without a transaction.
        """
        if not (isinstance(error, Validation) and plan.owner_changes and plan.grants_change):
            return ""
        name = quoted(plan.current.name)
        try:
            [now] = await self._reader.get_permissions(kind, [plan.current.id])
        except PaperlessMcpError, ValueError:
            return f"Whether the grants of {name} were set all the same is not known."
        if _normalised(now.permissions) == _normalised(plan.current.permissions):
            return ""
        if now.owner != plan.current.owner:
            return f"The owner and the grants of {name} were changed all the same."
        return f"The grants of {name} were set all the same; its owner was not changed."

    def _result(
        self,
        kind: PermissionKind,
        mode: Mode,
        plans: Sequence[_Plan],
        written: dict[int, ObjectAccess],
        names: dict[PrincipalKind, dict[int, str]],
    ) -> PermissionsResult:
        rows = tuple(
            ObjectResult(
                id=plan.current.id,
                name=plan.current.name,
                web_url=document_link(self._web_url, plan.current.id)
                if kind == "document"
                else None,
                changed=plan.current.id in written,
                access=access_view(written.get(plan.current.id, plan.current), names),
            )
            for plan in plans
        )
        changed = [row for row in rows if row.changed]
        label = LABELS[kind]
        if changed:
            message = (
                f"Changed the permissions of {listed([row.name for row in changed])} ({mode})."
            )
            same = len(rows) - len(changed)
            if same:
                message += f" {same} already {'was' if same == 1 else 'were'} as asked."
        else:
            only = len(rows) == 1
            message = (
                f"Nothing changed: {_plural(len(rows), label)} already "
                f"{'is' if only else 'are'} as asked."
            )
        return PermissionsResult(
            kind=kind,
            mode=mode,
            changed_count=len(changed),
            unchanged_count=len(rows) - len(changed),
            objects=rows,
            message=message,
        )

    @staticmethod
    def _effect(
        kind: PermissionKind,
        mode: Mode,
        changing: Sequence[_Plan],
        names: dict[PrincipalKind, dict[int, str]],
        caller: int | None,
    ) -> str:
        """What confirming would do, naming the first ``SHOWN`` objects and counting the rest."""
        label = LABELS[kind]
        verb = (
            "replace" if mode == "replace" and any(p.grants_change for p in changing) else "change"
        )
        subject = _plural(len(changing), label)
        head = f"{verb} the permissions of {subject} {listed([p.current.name for p in changing])}"
        several = len(changing) > 1
        details: list[str] = []
        for plan in changing[:SHOWN]:
            parts = _delta(plan, names, kind, caller)
            prefix = f"{quoted(plan.current.name)}: " if several else ""
            details.append(prefix + "; ".join(parts))
        text = ". ".join(details)
        if len(changing) > SHOWN:
            text += f". {len(changing) - SHOWN} more would change as well"
        if kind in NOTES and any(map(_hands_over, changing)):
            text += f". {NOTES[kind]}"
        return f"{head}: {text}"


def _cached(kind: PermissionKind) -> tuple[SharedKind, ...]:
    """The kinds of taxonomy whose cached rows a change may make wrong."""
    if kind == "document":
        return COUNTED
    return (SHARED[kind],) if kind in SHARED else ()


def _hands_over(plan: _Plan) -> bool:
    """Whether somebody who may not change the object now may then."""
    before, after = plan.current.permissions, plan.grants
    gains = set(after.change_users) - set(before.change_users)
    return plan.owner_changes or bool(gains or set(after.change_groups) - set(before.change_groups))


def _halfway(  # noqa: PLR0913 -- what a failure leaves behind
    error: PaperlessMcpError,
    plan: _Plan,
    plans: Sequence[_Plan],
    written: dict[int, ObjectAccess],
    *,
    unknown: bool,
    grants: str = "",
) -> str:
    """The message of a failure among several writes: what changed and what is not known."""
    parts = [error.message]
    if grants:
        parts.append(grants)
    if written:
        done = listed([p.current.name for p in plans if p.current.id in written])
        parts.append(f"Permissions were already changed on {done}.")
    if unknown:
        parts.append(UNKNOWN_STATE.format(quoted(plan.current.name)))
    elif not grants:
        parts.append(f"{quoted(plan.current.name)} and the objects after it were not changed.")
    elif plan is not plans[-1]:
        parts.append("The objects after it were not changed.")
    return " ".join(parts)


def _rebuilt(error: PaperlessMcpError, message: str) -> PaperlessMcpError:
    """An error like ``error`` with ``message``: same class, hint and HTTP status."""
    if isinstance(error, UpstreamUnavailable):
        return type(error)(message, hint=error.hint, status=error.status)
    return type(error)(message, hint=error.hint)


def _unseen(kind: PermissionKind, missing: list[int]) -> NotFound:
    label = LABELS[kind]
    noun = "id" if len(missing) == 1 else "ids"
    message = f"No {label} with {noun} {', '.join(map(str, missing))} exists that you may see."
    hint = (
        f"Find the ids with {FINDERS[kind]}."
        if kind in FINDERS
        else "Call list_taxonomy to see what exists."
    )
    return NotFound(message, hint=hint)


def _plan(row: ObjectAccess, asked: _Asked, owner: _Owner | None, mode: Mode) -> _Plan:
    grants = _new_grants(row.permissions, asked, mode)
    return _Plan(
        current=row,
        grants=grants,
        owner=None if owner is None else owner[0],
        owner_changes=owner is not None and owner[0] != row.owner,
    )


def _change(plan: _Plan) -> AccessChange:
    """What is sent for a plan: the owner only if it changes, the lists whole if they change."""
    fields: dict[str, object] = {}
    if plan.owner_changes:
        fields["owner"] = plan.owner
    if plan.grants_change:
        fields["permissions"] = plan.grants
    return AccessChange.model_validate(fields)


# The buckets of a change of access, as (level before, level after) pairs.
BUCKETS: Final[tuple[tuple[str, tuple[tuple[int, int], ...]], ...]] = (
    ("gains view", ((NONE, VIEW),)),
    ("gains change", ((NONE, CHANGE), (VIEW, CHANGE))),
    ("loses change (keeps view)", ((CHANGE, VIEW),)),
    ("loses view", ((VIEW, NONE),)),
    ("loses view and change", ((CHANGE, NONE),)),
)


def _owner_part(
    plan: _Plan, users: dict[int, str], kind: PermissionKind, caller: int | None
) -> str:
    """The phrase for an owner change: who loses and who takes what, and the caller's own risk."""
    label = LABELS[kind]
    before = plan.current.owner
    if before is None:
        old = "none"
        lost = (
            f"the {label} stops being visible to everyone who saw it through the global "
            "permission; "
        )
    else:
        old = quoted(users[before])
        lost = f"{old} loses the control ownership gave; "
    if plan.owner is None:
        return (
            f"the owner {old} is removed ({lost}every user with the global permission to view "
            f"{label}s can then see it and every user with the global permission to change "
            f"{label}s can change it)"
        )
    risk = "" if plan.owner == caller else "; you may lose access to it"
    return (
        f"the owner changes from {old} to {quoted(users[plan.owner])} "
        f"({lost}the new owner controls it{risk})"
    )


def _delta(
    plan: _Plan,
    names: dict[PrincipalKind, dict[int, str]],
    kind: PermissionKind,
    caller: int | None,
) -> list[str]:
    """The owner change and who gains and loses access, as phrases."""
    parts: list[str] = []
    users, groups = names["user"], names["group"]
    if plan.owner_changes:
        parts.append(_owner_part(plan, users, kind, caller))
    if plan.grants_change:
        old, new = plan.current.permissions, plan.grants
        moves = {
            "user": (
                _levels(old.view_users, old.change_users),
                _levels(new.view_users, new.change_users),
            ),
            "group": (
                _levels(old.view_groups, old.change_groups),
                _levels(new.view_groups, new.change_groups),
            ),
        }
        for text, pairs in BUCKETS:
            who: list[str] = []
            for principal, (before_levels, after_levels) in moves.items():
                table = users if principal == "user" else groups
                for number in sorted({*before_levels, *after_levels}):
                    move = (before_levels.get(number, NONE), after_levels.get(number, NONE))
                    if move in pairs:
                        who.append(f"{principal} {quoted(table[number])}")
            if who:
                parts.append(f"{text}: {', '.join(who)}")
    return parts
