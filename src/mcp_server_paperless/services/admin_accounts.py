"""Users and groups: reading, creating, changing and deleting them (decision-26).

Everything that gives someone more access, takes access away or removes an account needs
``confirm`` and the refusal says who is affected (decision-15, decision-19): superuser and staff
status, deactivating, a new password, group membership, permissions, and every deletion. Names,
e-mail addresses and the plain fields of a group need none. A password is write-only (decision-18):
it is checked here, goes to the port as a ``SecretStr`` and is in no result, message or log line.
The account this server signs in with can be neither deleted, deactivated nor stripped of its
superuser status, because the server could not undo that.
"""

from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.admin import (
    Deletion,
    GroupDetail,
    GroupOutcome,
    GroupValues,
    NotText,
    UserDetail,
    UserOutcome,
    UserValues,
    check_secret,
)
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    PaperlessMcpError,
    Validation,
    destination,
    quoted,
)
from mcp_server_paperless.domain.permissions import Principal
from mcp_server_paperless.services.principals import names_of, resolve_principals

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import SecretStr

    from mcp_server_paperless.domain.admin import GroupRecord, UserRecord
    from mcp_server_paperless.domain.documents import NameOrId
    from mcp_server_paperless.domain.ports import AdminReader, AdminWriter
    from mcp_server_paperless.services.principals import PrincipalKind
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

SUPERUSER: Final = "a superuser, who sees and changes everything whatever is granted"
CREATE_SUPERUSER: Final = "as a superuser, who sees and changes everything whatever is granted"
CREATE_STAFF: Final = (
    "with staff status, which lets them sign in to the Django admin site and read the logs"
)
STAFF: Final = "staff status, which lets them sign in to the Django admin site and read the logs"
SELF_MESSAGE: Final = (
    "{name} is the account this server signs in with; {what} would lock this server out of "
    "Paperless."
)
CANNOT_TELL: Final = (
    "Could not find out which account this server signs in with, so nothing that could lock "
    "it out was done."
)
CANNOT_TELL_HINT: Final = (
    "Try again in a moment; changes that cannot affect the server's own account (names, "
    "e-mail, groups, permissions) are not held back."
)
RENAME_TAIL: Final = "they sign in under the new name"
EMAIL_TAIL: Final = "whoever controls it can reset the password and take the account over"
SELF_HINT: Final = "Ask another administrator to do this in the Paperless web UI."
NOTHING_GIVEN: Final = "Nothing to change: no field was given."


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(quoted(value) for value in values)


class AccountAdmin:
    """Reads and changes users and groups, with the confirmations decision-26 asks for."""

    def __init__(
        self, reader: AdminReader, writer: AdminWriter, taxonomy: TaxonomyResolver
    ) -> None:
        """Read and write through the ports; resolve names with ``taxonomy``."""
        self._reader: AdminReader = reader
        self._writer: AdminWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy

    async def _caller(self) -> int:
        """The id of the account this server signs in with; it fails closed.

        Called only before a change that could lock that account out. If Paperless does not
        say who it is, whatever the reason (a timeout, a 5xx, a missing permission), the change
        is refused: the server could not undo it.

        Raises:
            Validation: The account cannot be determined.
        """
        try:
            return await self._reader.get_current_user_id()
        except PaperlessMcpError:
            raise Validation(CANNOT_TELL, hint=CANNOT_TELL_HINT) from None

    async def _group_names(self, ids: Sequence[int]) -> list[str]:
        known = await names_of(self._taxonomy, "group", ids)
        return [known[number] for number in ids]

    async def _one(self, kind: PrincipalKind, value: NameOrId) -> int:
        found = await resolve_principals(self._taxonomy, kind, [value])
        return found.ids[0]

    async def _detail(self, record: UserRecord) -> UserDetail:
        groups = await names_of(self._taxonomy, "group", record.group_ids)
        return UserDetail(
            id=record.id,
            username=record.username,
            email=record.email,
            first_name=record.first_name,
            last_name=record.last_name,
            is_active=record.is_active,
            is_staff=record.is_staff,
            is_superuser=record.is_superuser,
            groups=tuple(Principal(id=n, name=groups[n]) for n in record.group_ids),
            permissions=record.permissions,
            inherited_permissions=record.inherited_permissions,
            date_joined=record.date_joined,
            mfa_enabled=record.mfa_enabled,
        )

    @staticmethod
    def _group_detail(record: GroupRecord) -> GroupDetail:
        return GroupDetail(id=record.id, name=record.name, permissions=record.permissions)

    @staticmethod
    def _check_password(password: SecretStr) -> None:
        if isinstance(password, NotText):
            message = "The password must be text."
            raise Validation(message, hint="Pass the password as a string.")
        secret = password.get_secret_value()
        if not secret.strip():
            message = "The password must not be empty."
            raise Validation(message, hint="Pass a password, or leave it out.")
        check_secret(secret, "password")

    async def get_user(self, user: NameOrId) -> UserDetail:
        """One user by username or id.

        Raises:
            NotFound: No such user.
            PermissionDenied: The user may not view users.
        """
        return await self._detail(await self._reader.get_user(await self._one("user", user)))

    async def get_group(self, group: NameOrId) -> GroupDetail:
        """One group by name or id.

        Raises:
            NotFound: No such group.
            PermissionDenied: The user may not view groups.
        """
        return self._group_detail(await self._reader.get_group(await self._one("group", group)))

    async def create_user(  # noqa: PLR0913 -- one field each, keyword-only
        self,
        *,
        username: str,
        password: SecretStr | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        is_active: bool | None = None,
        is_staff: bool | None = None,
        is_superuser: bool | None = None,
        groups: Sequence[NameOrId] | None = None,
        permissions: Sequence[str] | None = None,
        confirm: bool = False,
    ) -> UserOutcome:
        """Create a user; a superuser or staff user needs ``confirm``.

        Raises:
            ConfirmationRequired: ``is_superuser`` or ``is_staff`` without ``confirm``.
            Validation: A bad password, username, e-mail address or permission.
            NotFound: A group does not exist.
        """
        if password is not None:
            self._check_password(password)
        group_ids = (
            None
            if groups is None
            else (await resolve_principals(self._taxonomy, "group", groups)).ids
        )
        parts = [
            text
            for flag, text in (
                (is_superuser, CREATE_SUPERUSER),
                (is_staff, CREATE_STAFF),
                (password is not None, "with a password, with which whoever knows it signs in"),
            )
            if flag
        ]
        if group_ids:
            parts.append(f"in the groups {_quoted(await self._group_names(group_ids))}")
        if permissions:
            parts.append(f"with the permissions {_quoted(permissions)}")
        if parts and not confirm:
            effect = f"create user {quoted(username)} {'; '.join(parts)}"
            ConfirmationRequired.require(effect, scope=group_ids)
        given = {
            "username": username,
            "password": password,
            "email": email or None,
            "first_name": first_name,
            "last_name": last_name,
            "is_active": is_active,
            "is_staff": is_staff,
            "is_superuser": is_superuser,
            "group_ids": group_ids,
            "permissions": None if permissions is None else tuple(permissions),
        }
        values = UserValues.model_validate({k: v for k, v in given.items() if v is not None})
        made = await self._writer.create_user(values)
        self._taxonomy.invalidate("user")
        return UserOutcome(
            user=await self._detail(made),
            changed=True,
            message=f"Created user {quoted(made.username)} (id {made.id}).",
        )

    async def update_user(  # noqa: PLR0913 -- one field each; one diff per field
        self,
        user: NameOrId,
        *,
        username: str | None = None,
        password: SecretStr | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        is_active: bool | None = None,
        is_staff: bool | None = None,
        is_superuser: bool | None = None,
        groups: Sequence[NameOrId] | None = None,
        permissions: Sequence[str] | None = None,
        confirm: bool = False,
    ) -> UserOutcome:
        """Change the fields given; access changes need ``confirm``.

        Raises:
            ConfirmationRequired: The change gives or takes access without ``confirm``.
            Validation: Nothing given, a bad value, or a change that would lock the server out.
            NotFound: The user or a group does not exist.
        """
        if password is not None:
            self._check_password(password)
        number = await self._one("user", user)
        current = await self._reader.get_user(number)
        who = quoted(current.username)
        group_ids = None
        if groups is not None:
            group_ids = (await resolve_principals(self._taxonomy, "group", groups)).ids
        given: dict[str, object] = {
            "username": username,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "is_active": is_active,
            "is_staff": is_staff,
            "is_superuser": is_superuser,
        }
        if all(v is None for v in given.values()) and (
            password is None and group_ids is None and permissions is None
        ):
            raise Validation(NOTHING_GIVEN, hint="Pass at least one field to change.")
        if (is_superuser is False or is_active is False) and await self._caller() == number:
            what = "removing its superuser status or deactivating it"
            message = SELF_MESSAGE.format(name=who, what=what)
            raise Validation(message, hint=SELF_HINT)
        changes, effects = await self._user_changes(
            current, who, given, password=password, groups=group_ids, permissions=permissions
        )
        if not changes:
            return UserOutcome(
                user=await self._detail(current),
                changed=False,
                message=f"User {who} already is as asked; nothing was changed.",
            )
        if effects and not confirm:
            ConfirmationRequired.require("; ".join(effects), scope=(current, changes))
        made = await self._writer.update_user(number, UserValues.model_validate(changes))
        self._taxonomy.invalidate("user")
        shown = ", ".join(changes)
        return UserOutcome(
            user=await self._detail(made),
            changed=True,
            message=f"Updated user {quoted(made.username)}: {shown}.",
        )

    async def _user_changes(  # noqa: PLR0913, PLR0912, C901 -- one diff per field
        self,
        current: UserRecord,
        who: str,
        given: dict[str, object],
        *,
        password: SecretStr | None,
        groups: Sequence[int] | None,
        permissions: Sequence[str] | None,
    ) -> tuple[dict[str, object], list[str]]:
        """What differs from ``current`` and the effects of it that need a confirmation."""
        group_ids = groups
        is_superuser, is_staff, is_active = (
            given["is_superuser"],
            given["is_staff"],
            given["is_active"],
        )
        email = given["email"]
        changes: dict[str, object] = {}
        compared = {**given, "email": email or None}
        changes.update(
            {
                name: value
                for name, value in compared.items()
                if value is not None and value != getattr(current, name)
            }
        )
        if email == "" and current.email is not None:
            changes["email"] = None
        effects: list[str] = []
        if "username" in changes:
            new_name = quoted(str(changes["username"]))
            effects.append(f"rename user {who} to {new_name}; {RENAME_TAIL}")
        if "email" in changes and changes["email"] is not None:
            verb = "change" if current.email else "set"
            address = destination(str(changes["email"]), "e-mail address")
            effects.append(f"{verb} the e-mail address of {who} to {address}; {EMAIL_TAIL}")
        if "is_superuser" in changes:
            effects.append(
                f"make {who} {SUPERUSER}" if is_superuser else f"remove superuser status from {who}"
            )
        if "is_staff" in changes:
            effects.append(f"give {who} {STAFF}" if is_staff else f"remove staff status from {who}")
        if is_active is False and current.is_active:
            effects.append(f"deactivate {who}, who can then no longer sign in")
        if is_active is True and not current.is_active:
            effects.append(f"reactivate {who}, who can then sign in again")
        if password is not None:
            effects.append(f"set a new password for {who}, with which whoever knows it signs in")
        if group_ids is not None and set(group_ids) != set(current.group_ids):
            changes["group_ids"] = tuple(group_ids)
            known = await names_of(self._taxonomy, "group", [*group_ids, *current.group_ids])
            added = [known[n] for n in group_ids if n not in current.group_ids]
            removed = [known[n] for n in current.group_ids if n not in group_ids]
            if added:
                effects.append(f"add {who} to the groups {_quoted(added)}")
            if removed:
                effects.append(f"remove {who} from the groups {_quoted(removed)}")
        if permissions is not None and set(permissions) != set(current.permissions):
            changes["permissions"] = tuple(permissions)
            added = [p for p in permissions if p not in current.permissions]
            removed = [p for p in current.permissions if p not in permissions]
            if added:
                effects.append(f"grant {who} the permissions {_quoted(added)}")
            if removed:
                effects.append(f"take the permissions {_quoted(removed)} away from {who}")
        if password is not None:
            changes["password"] = password
        return changes, effects

    async def delete_user(self, user: NameOrId, *, confirm: bool = False) -> Deletion:
        """Delete a user for good; needs ``confirm``.

        Raises:
            ConfirmationRequired: ``confirm`` is not set.
            Validation: The user is the account the server signs in with.
            NotFound: No such user.
        """
        number = await self._one("user", user)
        current = await self._reader.get_user(number)
        who = quoted(current.username)
        if await self._caller() == number:
            message = SELF_MESSAGE.format(name=who, what="deleting it")
            raise Validation(message, hint=SELF_HINT)
        if not confirm:
            rank = "; the user is a superuser" if current.is_superuser else ""
            effect = (
                f"delete user {who} for good. Documents and other objects they own lose their "
                "owner (every user with the permission may then see them), their grants are "
                f"removed and they can no longer sign in{rank}"
            )
            ConfirmationRequired.require(effect, scope=current)
        await self._writer.delete_user(number)
        self._taxonomy.invalidate("user")
        return Deletion(id=number, name=current.username, message=f"Deleted user {who}.")

    async def create_group(
        self, name: str, permissions: Sequence[str] | None = None, *, confirm: bool = False
    ) -> GroupOutcome:
        """Create a group; one that grants permissions needs ``confirm``.

        A group without permissions grants nothing, so it needs none; with permissions it is the
        first step of an escalation (a user is then put into it), so it is confirmed like the
        change of a group's permissions.

        Raises:
            ConfirmationRequired: The group grants permissions and ``confirm`` is not set.
            Validation: The name is taken or a permission does not exist.
        """
        if permissions and not confirm:
            effect = (
                f"create group {quoted(name)} that grants its members the permissions "
                f"{_quoted(permissions)}"
            )
            # scope: the text names the group and every permission; nothing is stored yet.
            ConfirmationRequired.require(effect, scope=None)
        values = GroupValues(name=name, permissions=tuple(permissions or ()))
        made = await self._writer.create_group(values)
        self._taxonomy.invalidate("group")
        return GroupOutcome(
            group=self._group_detail(made),
            changed=True,
            message=f"Created group {quoted(made.name)} (id {made.id}).",
        )

    async def update_group(
        self,
        group: NameOrId,
        *,
        name: str | None = None,
        permissions: Sequence[str] | None = None,
        confirm: bool = False,
    ) -> GroupOutcome:
        """Rename a group or replace the permissions it grants; permissions need ``confirm``.

        Raises:
            ConfirmationRequired: The permissions change without ``confirm``.
            Validation: Nothing given, or Paperless rejects a value.
            NotFound: No such group.
        """
        if name is None and permissions is None:
            raise Validation(NOTHING_GIVEN, hint="Pass a new name or the permissions.")
        number = await self._one("group", group)
        current = await self._reader.get_group(number)
        who = quoted(current.name)
        changes: dict[str, object] = {}
        if name is not None and name != current.name:
            changes["name"] = name
        effects: list[str] = []
        if permissions is not None and set(permissions) != set(current.permissions):
            changes["permissions"] = tuple(permissions)
            added = [p for p in permissions if p not in current.permissions]
            removed = [p for p in current.permissions if p not in permissions]
            if added:
                effects.append(f"give the members of {who} the permissions {_quoted(added)}")
            if removed:
                effects.append(f"take the permissions {_quoted(removed)} away from them")
        if not changes:
            return GroupOutcome(
                group=self._group_detail(current),
                changed=False,
                message=f"Group {who} already is as asked; nothing was changed.",
            )
        if effects and not confirm:
            ConfirmationRequired.require("; ".join(effects), scope=(current, changes))
        made = await self._writer.update_group(number, GroupValues.model_validate(changes))
        self._taxonomy.invalidate("group")
        return GroupOutcome(
            group=self._group_detail(made),
            changed=True,
            message=f"Updated group {quoted(made.name)}: {', '.join(changes)}.",
        )

    async def delete_group(self, group: NameOrId, *, confirm: bool = False) -> Deletion:
        """Delete a group for good; needs ``confirm``.

        Raises:
            ConfirmationRequired: ``confirm`` is not set.
            NotFound: No such group.
        """
        number = await self._one("group", group)
        current = await self._reader.get_group(number)
        who = quoted(current.name)
        if not confirm:
            effect = (
                f"delete group {who} for good; its members lose the permissions it grants and "
                "objects shared with the group are no longer shared with them"
            )
            ConfirmationRequired.require(effect, scope=current)
        await self._writer.delete_group(number)
        self._taxonomy.invalidate("group")
        return Deletion(id=number, name=current.name, message=f"Deleted group {who}.")
