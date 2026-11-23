"""Mail import: accounts, rules and processed mail behind the confirmation flow (design §6, §7).

Credentials never flow back (decision-6): a password goes in as a ``SecretStr`` and nothing the
service returns, raises or logs has one, nor any mask of one. A connection test and a fetch contact
the mail server with the saved password; the fetch also acts on the mail there, so both are
described plainly. What needs ``confirm`` (decision-15, decision-17):

- deleting an account (its rules and their processed-mail records go with it) or a rule;
- fetching an account now: depending on the rules it imports documents and deletes, moves, flags,
  tags or marks the mail on the server, which this server cannot undo;
- a rule that is enabled and deletes or moves mail, when it is created or when a change widens
  what it reaches (its account, folder, filters, age, action, parameter, or switching it on);
- pointing an account at another server, port or security setting without giving a new password
  (Paperless would send the saved one there), and turning encryption off;
- handing a rule over to another owner or to nobody (decision-12).

Taxonomy (tags, correspondent, document type, owner) is given by name or id and resolved through
the ``TaxonomyResolver`` (decision-5). Updates are diffs against the rule read right before.
"""

from typing import TYPE_CHECKING, Final, cast

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.mail import (
    ACTIONS_REMOVING_MAIL,
    ACTIONS_WITH_PARAMETER,
    MailAccountDeletion,
    MailAccountInfo,
    MailAccountListing,
    MailAccountTested,
    MailAccountValues,
    MailFetchStarted,
    MailRuleDeletion,
    MailRuleInfo,
    MailRuleListing,
    MailRuleValues,
    NotText,
    ProcessedMailListing,
    ProcessedMailRow,
    Reference,
    check_password,
)
from mcp_server_paperless.services.documents import owner_id
from mcp_server_paperless.services.sharing import plural

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import SecretStr

    from mcp_server_paperless.domain.documents import NameOrId
    from mcp_server_paperless.domain.mail import (
        ImapSecurity,
        MailAccount,
        MailRule,
        MailRuleSettings,
        ProcessedMail,
        ProcessedStatus,
        RuleAction,
        RuleClearable,
    )
    from mcp_server_paperless.domain.ports import InstanceReader, MailReader, MailWriter
    from mcp_server_paperless.domain.taxonomy import TaxonomyKind
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

MAX_ERROR: Final = 200
# Rule settings that decide which mails a rule acts on, and how: a change of one widens or
# redirects what a rule that deletes or moves mail does on the server.
REACH: Final = (
    *("enabled", "account_id", "folder", "maximum_age", "action", "action_parameter"),
    *("consumption_scope", "attachment_type"),
    *("filter_from", "filter_to", "filter_subject", "filter_body"),
    *("filter_attachment_filename_include", "filter_attachment_filename_exclude"),
)
SECURITY_LABEL: Final[Mapping[str, str]] = {
    "none": "no encryption",
    "ssl": "SSL",
    "starttls": "STARTTLS",
}
FILTERS: Final = (
    ("filter_from", "from {}"),
    ("filter_to", "to {}"),
    ("filter_subject", "with subject {}"),
    ("filter_body", "with text {}"),
    ("filter_attachment_filename_include", "with attachments named {}"),
    ("filter_attachment_filename_exclude", "without attachments named {}"),
)
FILTER_FIELDS: Final = tuple(name for name, _ in FILTERS)
# The names of the settings in MailRuleSettings that are plain values of MailRuleValues.
PLAIN: Final = (
    *("name", "enabled", "order", "folder", "attachment_type", "consumption_scope", "pdf_layout"),
    *("assign_title_from", "assign_correspondent_from", "assign_owner_from_rule"),
    *("stop_processing", "action"),
)


def _page[T](items: Sequence[T], page: int, page_size: int) -> tuple[Sequence[T], int | None]:
    """The items of ``page`` and the next page number, None on the last.

    Raises:
        NotFound: ``page`` is beyond the last page.
    """
    start = (page - 1) * page_size
    if page > 1 and start >= len(items):
        last = max(1, -(-len(items) // page_size))
        message = f"There is no page {page}: the list has {plural(last, 'page')}."
        raise NotFound(message, hint="Start again at page 1.")
    more = start + page_size < len(items)
    return items[start : start + page_size], page + 1 if more else None


def _line(text: str | None) -> str | None:
    """``text`` as one line cut to ``MAX_ERROR`` characters; None stays None."""
    if text is None:
        return None
    line = " ".join(text.split())
    return line if len(line) <= MAX_ERROR else line[: MAX_ERROR - 1] + "…"


def attribute(obj: object, name: str) -> object:
    """The attribute ``name`` of ``obj`` as a plain object (the names come from fixed tuples)."""
    return cast("object", getattr(obj, name))


def _blank(value: str | None) -> str | None:
    """An empty string means "none" for the filters and the action parameter."""
    return value or None


def _connection(server: str, port: int, security: ImapSecurity) -> str:
    return f"{server} port {port} ({SECURITY_LABEL[security]})"


def _does(action: RuleAction, parameter: str | None) -> str:
    """What a rule does to a mail on the mail server, as a verb phrase."""
    shown = quoted(parameter or "")
    return {
        "delete": "deletes the mail",
        "move": f"moves the mail to {shown}",
        "mark_read": "marks the mail as read",
        "flag": "flags the mail",
        "tag": f"tags the mail {shown}",
    }[action]


def _removes(*, enabled: bool, action: RuleAction | None) -> bool:
    return enabled and action in ACTIONS_REMOVING_MAIL


def _reach(rule: MailRuleValues | MailRule) -> str:
    """Which mails a rule looks at: folder, age and filters, as prose."""
    parts = [f"It covers mails in folder {quoted(rule.folder or 'INBOX')}"]
    # Paperless defaults an unset age to 30 days and filters by age only when it is above zero.
    days = 30 if rule.maximum_age is None else rule.maximum_age
    parts.append(f"up to {plural(days, 'day')} old" if days > 0 else "of any age")
    text = " ".join(parts)
    filters = [
        template.format(quoted(value))
        for name, template in FILTERS
        if isinstance(value := attribute(rule, name), str)
    ]
    return ", ".join([text, *filters])


class MailService:
    """Mail accounts, rules and processed mail for the current user."""

    def __init__(
        self,
        reader: MailReader,
        writer: MailWriter,
        taxonomy: TaxonomyResolver,
        instance: InstanceReader,
    ) -> None:
        """Read through ``reader``, write through ``writer``, resolve names through ``taxonomy``.

        ``instance`` tells who the caller is: a fetch also runs the rules of other users, and a
        rule created for another owner needs a yes.
        """
        self._instance: InstanceReader = instance
        self._reader: MailReader = reader
        self._writer: MailWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy

    # --- accounts

    @staticmethod
    def _account(account: MailAccount) -> MailAccountInfo:
        return MailAccountInfo(
            id=account.id,
            name=account.name,
            imap_server=account.imap_server,
            imap_port=account.imap_port,
            imap_security=account.imap_security,
            username=account.username,
            character_set=account.character_set,
            account_type=account.account_type,
            is_token=account.is_token,
            owner=account.owner,
            can_change=account.can_change,
            token_expires=account.expiration,
        )

    async def list_accounts(self, *, page: int, page_size: int) -> MailAccountListing:
        """One page of the mail accounts.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user may not view mail accounts.
        """
        accounts = await self._reader.list_mail_accounts()
        shown, following = _page(accounts, page, page_size)
        return MailAccountListing(
            count=len(accounts),
            page=page,
            next_page=following,
            accounts=tuple(self._account(account) for account in shown),
        )

    async def get_account(self, account_id: int) -> MailAccountInfo:
        """One mail account.

        Raises:
            NotFound: No such account.
            PermissionDenied: The user may not view mail accounts.
        """
        return self._account(await self._reader.get_mail_account(account_id))

    async def create_account(  # noqa: PLR0913 -- the settings of an account
        self,
        *,
        name: str,
        imap_server: str,
        imap_port: int,
        username: str,
        password: SecretStr,
        imap_security: ImapSecurity | None,
        character_set: str | None,
    ) -> MailAccountInfo:
        """Create an account; the password is stored by Paperless and never returned.

        Raises:
            Validation: A blank or asterisk-only password, or Paperless rejects a value.
            PermissionDenied: The user may not add mail accounts.
        """
        self._check_password(password)
        values = MailAccountValues(
            name=name,
            imap_server=imap_server,
            imap_port=imap_port,
            username=username,
            password=password,
        )
        if imap_security is not None:
            values = values.model_copy(update={"imap_security": imap_security})
        if character_set is not None:
            values = values.model_copy(update={"character_set": character_set})
        return self._account(await self._writer.create_mail_account(values))

    @staticmethod
    def _check_password(password: SecretStr) -> None:
        if isinstance(password, NotText):
            message = "The password must be text."
            raise Validation(message, hint="Pass the password as a string.")
        secret = password.get_secret_value()
        if not secret.strip():
            message = "The password must not be empty."
            raise Validation(message, hint="Pass the password of the mail account.")
        check_password(secret)

    async def update_account(  # noqa: PLR0913 -- the settings of an account
        self,
        account_id: int,
        *,
        name: str | None = None,
        imap_server: str | None = None,
        imap_port: int | None = None,
        imap_security: ImapSecurity | None = None,
        username: str | None = None,
        password: SecretStr | None = None,
        character_set: str | None = None,
        confirm: bool = False,
    ) -> MailAccountInfo:
        """Change an account; only what differs is written, and the password stays unless given.

        Raises:
            ConfirmationRequired: The change points the account at another server, port or
                security setting without a new password, or turns encryption off, and
                ``confirm`` is false. Nothing was changed.
            Validation: Nothing to change, a blank or asterisk-only password, or Paperless
                rejects a value.
            NotFound: No such account.
            PermissionDenied: The user may not change this account.
        """
        given = {
            "name": name,
            "imap_server": imap_server,
            "imap_port": imap_port,
            "imap_security": imap_security,
            "username": username,
            "character_set": character_set,
        }
        if all(value is None for value in given.values()) and password is None:
            message = "Nothing to change: pass at least one setting."
            raise Validation(message, hint="Name the settings of the account to change.")
        if password is not None:
            self._check_password(password)
        current = await self._reader.get_mail_account(account_id)
        changes = {
            key: value for key, value in given.items() if value not in {None, getattr(current, key)}
        }
        if not changes and password is None:
            return self._account(current)
        effect = self._connection_effect(current, changes, new_password=password is not None)
        if effect and not confirm:
            raise ConfirmationRequired.for_effect(effect)
        values = MailAccountValues.model_validate(changes)
        if password is not None:
            values = values.model_copy(update={"password": password})
        return self._account(await self._writer.update_mail_account(account_id, values))

    @staticmethod
    def _connection_effect(
        current: MailAccount, changes: Mapping[str, object], *, new_password: bool
    ) -> str | None:
        """What a change of the connection does that the caller cannot see, or None."""
        server = str(changes.get("imap_server", current.imap_server))
        port = int(str(changes.get("imap_port", current.imap_port)))
        security: ImapSecurity = changes.get("imap_security", current.imap_security)  # pyright: ignore[reportAssignmentType] -- the changes come from typed arguments
        label = f"mail account {quoted(current.name)} (id {current.id})"
        effects: list[str] = []
        moved = {"imap_server", "imap_port", "imap_security"} & set(changes)
        if moved and not new_password:
            old = _connection(current.imap_server, current.imap_port, current.imap_security)
            moving = (
                f"point {label} at {_connection(server, port, security)} instead of {old}: "
                "Paperless keeps the saved password and sends it to the new server from its "
                "next fetch on"
            )
            effects.append(moving)
        if security == "none" and current.imap_security != "none":
            plain = (
                f"turn off the encryption of {label}: the password and the mail then travel "
                f"as plain text to {server} port {port}"
            )
            effects.append(plain)
        return "; and ".join(effects) or None

    async def _caller(self) -> tuple[int | None, bool]:
        """The id of the calling user (None if unknown) and whether they are a superuser."""
        user = (await self._instance.get_overview()).current_user
        return (None, False) if user is None else (user.id, user.is_superuser)

    async def _rules_of(self, account_id: int) -> list[MailRule] | None:
        """The rules of an account, or None if the user may not list rules."""
        try:
            rules = await self._reader.list_mail_rules()
        except PermissionDenied:
            return None
        return [rule for rule in rules if rule.account_id == account_id]

    async def delete_account(self, account_id: int, *, confirm: bool) -> MailAccountDeletion:
        """Delete an account with its rules; without ``confirm`` refuse and say what goes.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such account.
            PermissionDenied: The user may not delete this account.
        """
        account = await self._reader.get_mail_account(account_id)
        rules = await self._rules_of(account_id)
        label = f"mail account {quoted(account.name)} (id {account.id})"
        if rules is None:
            with_rules = " with its rules and the records of the mail they processed"
            done = " with its rules"
        elif rules:
            names = ", ".join(quoted(rule.name) for rule in rules)
            with_rules = (
                f" with its {plural(len(rules), 'rule')} ({names}) and the records of the mail "
                "they processed"
            )
            done = f" with its {plural(len(rules), 'rule')}"
        else:
            with_rules = done = ""
        if not confirm:
            effect = (
                f"delete {label}{with_rules}; Paperless stops fetching from it, the mail on the "
                "server stays"
            )
            raise ConfirmationRequired.for_effect(effect)
        await self._writer.delete_mail_account(account_id)
        message = (
            f"{label[0].upper()}{label[1:]} was deleted{done}; Paperless no longer fetches from it."
        )
        return MailAccountDeletion(id=account_id, message=message)

    async def test_account(self, account_id: int) -> MailAccountTested:
        """Connect to the account's mail server with its saved password.

        Raises:
            NotFound: No such account.
            PermissionDenied: The user may not change this account (Paperless asks for it).
        """
        account = await self._reader.get_mail_account(account_id)
        result = await self._writer.test_mail_account(account_id)
        return MailAccountTested(
            id=account_id,
            success=result.success,
            message=f"Mail account {quoted(account.name)}: {result.message}",
        )

    async def process_account(self, account_id: int, *, confirm: bool) -> MailFetchStarted:
        """Fetch the mail of an account now; without ``confirm`` refuse and say what rules do.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was started.
            NotFound: No such account.
            PermissionDenied: The user may not fetch for this account.
        """
        account = await self._reader.get_mail_account(account_id)
        label = quoted(account.name)
        if not confirm:
            rules = await self._rules_of(account_id)
            effect = (
                f"fetch the mail of account {label} ({account.imap_server}) now: "
                f"{self._fetch_effect(rules)}"
            )
            _, superuser = await self._caller()
            if not superuser:
                effect += ". Rules of other users on this account that you cannot see run too"
            raise ConfirmationRequired.for_effect(effect)
        await self._writer.process_mail_account(account_id)
        message = (
            f"Paperless queued a fetch of the mail of account {label}. It runs in the "
            "background: new documents appear after consumption (check the Paperless task list) "
            "and list_processed_mail shows the mails it handled."
        )
        return MailFetchStarted(id=account_id, message=message)

    @staticmethod
    def _fetch_effect(rules: Sequence[MailRule] | None) -> str:
        """What a fetch does, from the rules of the account (None: they could not be read)."""
        if rules is None:
            return (
                "Paperless connects to the mail server and imports documents; its rules could "
                "not be read, so what they do to the mail is unknown"
            )
        enabled = [rule for rule in rules if rule.enabled]
        if not enabled:
            return "the account has no enabled rule, so Paperless imports nothing"
        verb = "matches" if len(enabled) == 1 else "match"
        count = plural(len(enabled), "enabled rule")
        doing = "; ".join(
            f"rule {quoted(rule.name)} (id {rule.id}) {_does(rule.action, rule.action_parameter)}"
            for rule in enabled
        )
        text = (
            "Paperless connects to the mail server and imports documents from the mails that "
            f"its {count} {verb}, then acts on those mails on the server. {doing[0].upper()}"
            f"{doing[1:]}"
        )
        if any(rule.action == "delete" for rule in enabled):
            text += ". A deleted mail cannot be put back by this server"
        return text

    # --- rules

    async def _names(self, kind: TaxonomyKind) -> dict[int, str]:
        try:
            return {item.id: item.name for item in await self._taxonomy.items(kind)}
        except PermissionDenied:
            return {}

    async def _rule_infos(self, rules: Sequence[MailRule]) -> tuple[MailRuleInfo, ...]:
        """The rules with the names of what they assign, by one read per kind."""
        tags = await self._names("tag")
        correspondents = await self._names("correspondent")
        types = await self._names("document_type")
        users = await self._names("user")

        def ref(names: Mapping[int, str], number: int | None) -> Reference | None:
            return None if number is None else Reference(id=number, name=names.get(number))

        return tuple(
            MailRuleInfo(
                id=rule.id,
                name=rule.name,
                account_id=rule.account_id,
                enabled=rule.enabled,
                order=rule.order,
                folder=rule.folder,
                filter_from=rule.filter_from,
                filter_to=rule.filter_to,
                filter_subject=rule.filter_subject,
                filter_body=rule.filter_body,
                filter_attachment_filename_include=rule.filter_attachment_filename_include,
                filter_attachment_filename_exclude=rule.filter_attachment_filename_exclude,
                maximum_age_days=rule.maximum_age,
                attachment_type=rule.attachment_type,
                consumption_scope=rule.consumption_scope,
                pdf_layout=rule.pdf_layout,
                action=rule.action,
                action_parameter=rule.action_parameter,
                assign_title_from=rule.assign_title_from,
                assign_tags=tuple(Reference(id=n, name=tags.get(n)) for n in rule.assign_tags),
                assign_correspondent_from=rule.assign_correspondent_from,
                assign_correspondent=ref(correspondents, rule.assign_correspondent),
                assign_document_type=ref(types, rule.assign_document_type),
                assign_owner_from_rule=rule.assign_owner_from_rule,
                stop_processing=rule.stop_processing,
                owner=ref(users, rule.owner),
                can_change=rule.can_change,
            )
            for rule in rules
        )

    async def list_rules(
        self, *, account_id: int | None, page: int, page_size: int
    ) -> MailRuleListing:
        """One page of the mail rules, those of one account if ``account_id`` is given.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user may not view mail rules.
        """
        rules = [
            rule
            for rule in await self._reader.list_mail_rules()
            if account_id in {None, rule.account_id}
        ]
        shown, following = _page(rules, page, page_size)
        return MailRuleListing(
            count=len(rules),
            page=page,
            next_page=following,
            rules=await self._rule_infos(shown),
        )

    async def get_rule(self, rule_id: int) -> MailRuleInfo:
        """One mail rule.

        Raises:
            NotFound: No such rule.
            PermissionDenied: The user may not view mail rules.
        """
        [info] = await self._rule_infos([await self._reader.get_mail_rule(rule_id)])
        return info

    async def _tag_ids(self, values: Sequence[NameOrId]) -> tuple[int, ...]:
        ids = [(await self._taxonomy.resolve("tag", value)).id for value in values]
        return tuple(dict.fromkeys(ids))

    async def _resolved(
        self, settings: MailRuleSettings, clear: Sequence[RuleClearable]
    ) -> dict[str, object]:
        """The settings as MailRuleValues fields: names resolved, "" turned into None."""
        found: dict[str, object] = {
            name: value for name in PLAIN if (value := attribute(settings, name)) is not None
        }
        for name in FILTER_FIELDS:
            if isinstance(value := attribute(settings, name), str):
                found[name] = _blank(value)
        if settings.action_parameter is not None:
            found["action_parameter"] = _blank(settings.action_parameter)
        if settings.account_id is not None:
            found["account_id"] = settings.account_id
        if settings.maximum_age_days is not None:
            found["maximum_age"] = settings.maximum_age_days
        found.update(await self._named(settings))
        for name in clear:
            if name in found:
                message = f"{name} is both set and cleared in one call."
                raise Validation(message, hint=f"Either pass {name} or name it in clear.")
            found[name] = None
        return found

    async def _named(self, settings: MailRuleSettings) -> dict[str, object]:
        """The taxonomy in ``settings`` as ids."""
        found: dict[str, object] = {}
        if settings.assign_tags is not None:
            found["assign_tags"] = await self._tag_ids(settings.assign_tags)
        if settings.assign_correspondent is not None:
            found["assign_correspondent"] = (
                await self._taxonomy.resolve("correspondent", settings.assign_correspondent)
            ).id
        if settings.assign_document_type is not None:
            found["assign_document_type"] = (
                await self._taxonomy.resolve("document_type", settings.assign_document_type)
            ).id
        if settings.owner is not None:
            found["owner"] = await owner_id(self._taxonomy, settings.owner)
        return found

    async def _account_name(self, account_id: int) -> str:
        return (await self._reader.get_mail_account(account_id)).name

    @staticmethod
    def _check_action(action: RuleAction | None, parameter: str | None) -> None:
        if action in ACTIONS_WITH_PARAMETER and not parameter:
            what = "the folder to move the mail to" if action == "move" else "the tag to set"
            message = f"The action {action} needs action_parameter: {what}."
            raise Validation(message, hint=f"Pass action_parameter with {what}.")

    @staticmethod
    def _correspondent_source(values: dict[str, object]) -> None:
        """A named correspondent is used only with the source ``selected``: set it or refuse."""
        if "assign_correspondent" not in values or values["assign_correspondent"] is None:
            return
        source = values.get("assign_correspondent_from")
        if source == "selected":
            return
        if "assign_correspondent_from" in values:
            message = "assign_correspondent is used only with assign_correspondent_from selected."
            raise Validation(
                message, hint="Pass assign_correspondent_from selected, or leave it out."
            )
        values["assign_correspondent_from"] = "selected"  # a value equal to the rule's is no change

    @staticmethod
    def _rule_effect(rule: MailRuleValues | MailRule, verb: str, *, then: bool = False) -> str:
        """The prose that makes a rule which removes mail confirmable; ``verb`` starts it."""
        if rule.action == "delete":
            acts = "deletes each mail from the mail server"
            note = "; a deleted mail cannot be put back by this server"
        else:
            folder = quoted(rule.action_parameter or "")
            acts = f"moves each mail to the folder {folder} on the mail server"
            note = ""
        later = "then " if then else ""
        return (
            f"{verb}, which {later}{acts} after Paperless imported documents from it. "
            f"{_reach(rule)}{note}"
        )

    async def create_rule(self, settings: MailRuleSettings, *, confirm: bool) -> MailRuleInfo:
        """Create a rule; one that is enabled and deletes or moves mail needs ``confirm``.

        Raises:
            ConfirmationRequired: The rule removes mail and ``confirm`` is false.
            Validation: No name or account, a missing action parameter, a correspondent
                with another source, or Paperless rejects a value.
            NotFound: The account, a tag, correspondent, document type or user does not exist.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not add rules or change the account.
        """
        if not settings.name:
            message = "A mail rule needs a name."
            raise Validation(message, hint="Pass name.")
        if settings.account_id is None:
            message = "A mail rule needs an account."
            raise Validation(message, hint="Pass account_id; list_mail_accounts shows the ids.")
        account = await self._account_name(settings.account_id)
        values = await self._resolved(settings, ())
        self._correspondent_source(values)
        action = settings.action
        self._check_action(action, values.get("action_parameter"))  # pyright: ignore[reportArgumentType] -- from a typed setting
        written = MailRuleValues.model_validate(values)
        if not confirm:
            effects = await self._create_effects(settings.name, account, written, values)
            if effects:
                raise ConfirmationRequired.for_effect("; and ".join(effects))
        [info] = await self._rule_infos([await self._writer.create_mail_rule(written)])
        return info

    async def _create_effects(
        self, name: str, account: str, written: MailRuleValues, values: Mapping[str, object]
    ) -> list[str]:
        """What creating the rule does that needs ``confirm``: removing mail, another owner."""
        effects: list[str] = []
        removes = _removes(enabled=bool(values.get("enabled", True)), action=written.action)
        if removes:
            verb = f"create the enabled mail rule {quoted(name)} for account {quoted(account)}"
            effects.append(self._rule_effect(written, verb))
        owner = values.get("owner")
        if isinstance(owner, int) and owner != (await self._caller())[0]:
            who = await self._owner_name(owner)
            start = "it belongs to" if removes else f"create the mail rule {quoted(name)} for"
            takeover = "the owner takes it over, and you may not be able to change it again"
            effects.append(f"{start} {who}: {takeover}")
        return effects

    async def update_rule(
        self,
        rule_id: int,
        settings: MailRuleSettings,
        *,
        clear: Sequence[RuleClearable] = (),
        confirm: bool,
    ) -> MailRuleInfo:
        """Change a rule; only what differs is written.

        Raises:
            ConfirmationRequired: The change hands the rule to another owner, or the rule
                removes mail and the change widens what it reaches, and ``confirm`` is false.
            Validation: Nothing to change, a missing action parameter, or Paperless rejects a value.
            NotFound: The rule, an account or a taxonomy object does not exist.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not change this rule.
        """
        wanted = await self._resolved(settings, clear)
        if not wanted:
            message = "Nothing to change: pass at least one setting."
            raise Validation(message, hint="Name the settings of the rule to change.")
        current = await self._reader.get_mail_rule(rule_id)
        self._correspondent_source(wanted)
        self._action_changes(wanted, current)
        changes = {
            key: value for key, value in wanted.items() if self._differs(current, key, value)
        }
        if not changes:
            [info] = await self._rule_infos([current])
            return info
        if isinstance(changes.get("account_id"), int):
            _ = await self._account_name(int(str(changes["account_id"])))  # it must exist
        label = f"mail rule {quoted(current.name)} (id {current.id})"
        effects = await self._update_effects(current, changes, label)
        if effects and not confirm:
            raise ConfirmationRequired.for_effect("; and ".join(effects))
        written = await self._writer.update_mail_rule(
            rule_id, MailRuleValues.model_validate(changes)
        )
        [info] = await self._rule_infos([written])
        return info

    @staticmethod
    def _differs(current: MailRule, key: str, value: object) -> bool:
        """Whether ``value`` for the MailRuleValues field ``key`` differs from the rule."""
        return attribute(current, key) != value

    def _action_changes(self, wanted: dict[str, object], current: MailRule) -> None:
        """Check the action and its parameter together; drop a stale parameter with the action."""
        action: RuleAction = wanted.get("action", current.action)  # pyright: ignore[reportAssignmentType] -- from typed settings
        if "action" in wanted and wanted["action"] != current.action:
            if action in ACTIONS_WITH_PARAMETER:
                self._check_action(action, wanted.get("action_parameter"))  # pyright: ignore[reportArgumentType] -- from typed settings
            elif "action_parameter" not in wanted and current.action_parameter is not None:
                wanted["action_parameter"] = None  # Paperless would keep the old one
        elif "action_parameter" in wanted:
            self._check_action(action, wanted["action_parameter"])  # pyright: ignore[reportArgumentType] -- from typed settings

    async def _update_effects(
        self, current: MailRule, changes: dict[str, object], label: str
    ) -> list[str]:
        """What the update does that needs a yes, one sentence each."""
        effects: list[str] = []
        if "owner" in changes:
            effects.append(await self._owner_effect(label, changes["owner"]))  # pyright: ignore[reportArgumentType] -- an id or None
        result = current.model_copy(update=changes)
        if _removes(enabled=result.enabled, action=result.action) and any(
            key in changes for key in REACH
        ):
            account = await self._account_name(result.account_id)
            verb = f"change the enabled {label} of account {quoted(account)}"
            effects.append(self._rule_effect(result, verb, then=True))
        pacing = "order" in changes or changes.get("stop_processing") is False
        if pacing and result.enabled and (later := await self._later_effect(result, label)):
            effects.append(later)
        return effects

    async def _later_effect(self, rule: MailRule, label: str) -> str | None:
        """What a change of order or stop_processing lets other removing rules do, or None.

        Conservative: any other enabled rule of the account that deletes or moves mail counts, in
        front of this rule or behind it.
        """
        rules = await self._rules_of(rule.account_id) or []
        others = [
            r for r in rules if r.id != rule.id and _removes(enabled=r.enabled, action=r.action)
        ]
        if not others:
            return None
        named = " and ".join(f"{quoted(r.name)} (id {r.id})" for r in others)
        if len(others) == 1:
            subject = f"the enabled rule {named} of the same account, which"
            does = "deletes mail" if others[0].action == "delete" else "moves mail"
        else:
            subject = f"the enabled rules {named} of the same account, which"
            does = "delete or move mail"
        return (
            f"change the order or stop_processing of the enabled {label}, so that {subject} "
            f"{does}, may run for mails that {quoted(rule.name)} already imported from"
        )

    async def _owner_name(self, owner: int) -> str:
        names = await self._names("user")
        return f"user {quoted(names[owner])} (id {owner})" if owner in names else f"user {owner}"

    async def _owner_effect(self, label: str, owner: int | None) -> str:
        who = (
            "every user who may view mail rules" if owner is None else await self._owner_name(owner)
        )
        return (
            f"hand the {label} over to {who}: the new owner takes it over, and you may not be "
            "able to change it again"
        )

    async def delete_rule(self, rule_id: int, *, confirm: bool) -> MailRuleDeletion:
        """Delete a rule; without ``confirm`` refuse and say what goes.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such rule.
            PermissionDenied: The user may not delete this rule.
        """
        rule = await self._reader.get_mail_rule(rule_id)
        label = f"mail rule {quoted(rule.name)} (id {rule.id})"
        if not confirm:
            account = await self._account_name(rule.account_id)
            effect = (
                f"delete the {label} of account {quoted(account)} and the records of the mail "
                "it processed; Paperless stops importing with it, the mail on the server stays"
            )
            raise ConfirmationRequired.for_effect(effect)
        await self._writer.delete_mail_rule(rule_id)
        return MailRuleDeletion(id=rule_id, message=f"{label[0].upper()}{label[1:]} was deleted.")

    # --- processed mail

    @staticmethod
    def _row(mail: ProcessedMail) -> ProcessedMailRow:
        return ProcessedMailRow(
            id=mail.id,
            rule_id=mail.rule_id,
            subject=mail.subject,
            folder=mail.folder,
            received=mail.received,
            processed=mail.processed,
            status=mail.status,
            error=_line(mail.error),
        )

    async def list_processed(
        self, *, rule_id: int | None, status: ProcessedStatus | None, page: int, page_size: int
    ) -> ProcessedMailListing:
        """One page of the mails Paperless handled, newest first, as compact rows.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user may not view processed mail.
        """
        found = await self._reader.list_processed_mail(
            rule_id=rule_id, status=status, page=page, page_size=page_size
        )
        return ProcessedMailListing(
            count=found.count,
            page=page,
            next_page=found.next_page,
            mails=tuple(self._row(mail) for mail in found.mails),
        )
