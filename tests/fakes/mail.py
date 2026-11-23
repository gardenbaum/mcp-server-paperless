"""The mail part of FakePaperless: accounts, rules, processed mail, as Paperless 3.4.2 does.

Plain functions on the fake's state and user; `FakePaperless` delegates to them (and carries the
write guard), so `paperless.py` does not grow further.

- An account or rule belongs to its creator; others see it only as a superuser or when it has no
  owner. Every action needs the global permission of the model (`view_mailaccount`, ...).
- The fake never holds a password anywhere a port can read it: `FakeMailAccount.password` is read
  by `test_account` only, and no row built from an account has a password field.
- A password of asterisks only is refused like the adapter does (Paperless would ignore it).
- The mail server is imaginary: a login works if `MailData.logins` holds (server, port, user,
  password); a host in `hosts` answers but rejects other logins (400 in Paperless); a host in
  `silent_hosts` accepts the connection and never answers; any other host is unreachable (500).
  `process_account` records the account in `fetches`; the fake fetches no mail.
- Rule checks follow the serializer: `action` move or tag needs a non-blank `action_parameter`
  in the same request (checked only when the request names an action), the account must exist
  and be one the user may change, `maximum_age` is at most 36500, names are unique per owner,
  and tags, correspondent, document type and owner must exist.
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fakes.document_values import reject
from mcp_server_paperless.domain.errors import PermissionDenied, Validation
from mcp_server_paperless.domain.mail import (
    MailAccount,
    MailAccountTestResult,
    MailRule,
    ProcessedMail,
    ProcessedMailPage,
    check_password,
)
from mcp_server_paperless.paperless.mail import (
    beyond_last_page,
    connected,
    failed_login,
    silent_server,
    unreachable_server,
    unseen_account,
    unseen_rule,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from mcp_server_paperless.domain.mail import (
        AccountType,
        AttachmentScope,
        CorrespondentSource,
        ImapSecurity,
        MailAccountValues,
        MailRuleValues,
        MailScope,
        PdfLayout,
        ProcessedStatus,
        RuleAction,
        TitleSource,
    )

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
MAX_AGE = 36500


@dataclass(kw_only=True)
class FakeMailAccount:
    """A mail account; the password is stored but no port ever returns it."""

    id: int
    name: str
    imap_server: str
    imap_port: int
    username: str
    password: str
    imap_security: ImapSecurity = "ssl"
    character_set: str = "UTF-8"
    account_type: AccountType = "imap"
    is_token: bool = False
    owner: int | None = None
    expiration: datetime | None = None


@dataclass(kw_only=True)
class FakeMailRule:
    """A mail rule with Paperless's defaults."""

    id: int
    name: str
    account: int
    owner: int | None = None
    enabled: bool = True
    order: int = 0
    folder: str = "INBOX"
    filter_from: str | None = None
    filter_to: str | None = None
    filter_subject: str | None = None
    filter_body: str | None = None
    filter_attachment_filename_include: str | None = None
    filter_attachment_filename_exclude: str | None = None
    maximum_age: int = 30
    attachment_type: AttachmentScope = "attachments_only"
    consumption_scope: MailScope = "attachments"
    pdf_layout: PdfLayout = "default"
    action: RuleAction = "mark_read"
    action_parameter: str | None = None
    assign_title_from: TitleSource = "subject"
    assign_tags: tuple[int, ...] = ()
    assign_correspondent_from: CorrespondentSource = "none"
    assign_correspondent: int | None = None
    assign_document_type: int | None = None
    assign_owner_from_rule: bool = True
    stop_processing: bool = False


@dataclass(kw_only=True)
class FakeProcessedMail:
    """A mail Paperless handled, as a test sets it up."""

    id: int
    rule: int
    subject: str
    folder: str = "INBOX"
    received: datetime = NOW
    processed: datetime = NOW
    status: str = "SUCCESS"
    error: str | None = None
    owner: int | None = None


@dataclass(frozen=True)
class Viewer:
    """The acting user and the ids that exist (FakePaperless fills it in).

    Attributes:
        has: Django's `has_perm` for the user.
        users, tags, correspondents, document_types: Ids a rule may refer to.
    """

    id: int
    is_superuser: bool
    has: Callable[[str], bool]
    users: frozenset[int]
    tags: frozenset[int]
    correspondents: frozenset[int]
    document_types: frozenset[int]


@dataclass(frozen=True)
class MailData:
    """The mail part of the fake's state (lists and sets are shared with `FakeState`)."""

    accounts: list[FakeMailAccount]
    rules: list[FakeMailRule]
    processed: list[FakeProcessedMail]
    logins: set[tuple[str, int, str, str]]
    hosts: set[str]
    silent_hosts: set[str]
    fetches: list[int] = field(default_factory=list[int])


def denied() -> PermissionDenied:
    """Paperless's 403."""
    return PermissionDenied("The Paperless user may not perform this action.")


def _allow(user: Viewer, action: str, model: str) -> None:
    if not user.has(f"{action}_{model}"):
        raise denied()


def _visible(owner: int | None, user: Viewer) -> bool:
    return user.is_superuser or owner in {None, user.id}


def _may_change(owner: int | None, user: Viewer) -> bool:
    """Paperless's `user_can_change` for the row (object permissions only)."""
    return _visible(owner, user)


def _account(row: FakeMailAccount, user: Viewer) -> MailAccount:
    return MailAccount(
        id=row.id,
        name=row.name,
        imap_server=row.imap_server,
        imap_port=row.imap_port,
        imap_security=row.imap_security,
        username=row.username,
        character_set=row.character_set,
        account_type=row.account_type,
        is_token=row.is_token,
        owner=row.owner,
        can_change=_may_change(row.owner, user),
        expiration=row.expiration,
    )


def _rule(row: FakeMailRule, user: Viewer) -> MailRule:
    return MailRule(
        id=row.id,
        name=row.name,
        account_id=row.account,
        enabled=row.enabled,
        order=row.order,
        folder=row.folder,
        filter_from=row.filter_from,
        filter_to=row.filter_to,
        filter_subject=row.filter_subject,
        filter_body=row.filter_body,
        filter_attachment_filename_include=row.filter_attachment_filename_include,
        filter_attachment_filename_exclude=row.filter_attachment_filename_exclude,
        maximum_age=row.maximum_age,
        attachment_type=row.attachment_type,
        consumption_scope=row.consumption_scope,
        pdf_layout=row.pdf_layout,
        action=row.action,
        action_parameter=row.action_parameter,
        assign_title_from=row.assign_title_from,
        assign_tags=row.assign_tags,
        assign_correspondent_from=row.assign_correspondent_from,
        assign_correspondent=row.assign_correspondent,
        assign_document_type=row.assign_document_type,
        assign_owner_from_rule=row.assign_owner_from_rule,
        stop_processing=row.stop_processing,
        owner=row.owner,
        can_change=_may_change(row.owner, user),
    )


def _own_account(data: MailData, user: Viewer, account_id: int) -> FakeMailAccount:
    found = [r for r in data.accounts if r.id == account_id and _visible(r.owner, user)]
    if not found:
        raise unseen_account(account_id)
    return found[0]


def _own_rule(data: MailData, user: Viewer, rule_id: int) -> FakeMailRule:
    found = [r for r in data.rules if r.id == rule_id and _visible(r.owner, user)]
    if not found:
        raise unseen_rule(rule_id)
    return found[0]


def list_accounts(data: MailData, user: Viewer) -> tuple[MailAccount, ...]:
    """`MailReader.list_mail_accounts` (MailAccountViewSet list)."""
    _allow(user, "view", "mailaccount")
    return tuple(_account(r, user) for r in data.accounts if _visible(r.owner, user))


def get_account(data: MailData, user: Viewer, account_id: int) -> MailAccount:
    """`MailReader.get_mail_account` (MailAccountViewSet retrieve)."""
    _allow(user, "view", "mailaccount")
    return _account(_own_account(data, user, account_id), user)


def list_rules(data: MailData, user: Viewer) -> tuple[MailRule, ...]:
    """`MailReader.list_mail_rules` (MailRuleViewSet list, ordered by `order`)."""
    _allow(user, "view", "mailrule")
    rows = [r for r in data.rules if _visible(r.owner, user)]
    return tuple(_rule(r, user) for r in sorted(rows, key=lambda r: (r.order, r.id)))


def get_rule(data: MailData, user: Viewer, rule_id: int) -> MailRule:
    """`MailReader.get_mail_rule` (MailRuleViewSet retrieve)."""
    _allow(user, "view", "mailrule")
    return _rule(_own_rule(data, user, rule_id), user)


def list_processed(  # noqa: PLR0913 -- the port's signature
    data: MailData,
    user: Viewer,
    *,
    rule_id: int | None,
    status: ProcessedStatus | None,
    page: int,
    page_size: int,
) -> ProcessedMailPage:
    """`MailReader.list_processed_mail` (ProcessedMailViewSet list, newest processed first)."""
    _allow(user, "view", "processedmail")
    rows = [
        r
        for r in data.processed
        if _visible(r.owner, user)
        and rule_id in {None, r.rule}
        and (status is None or r.status == status)
    ]
    rows.sort(key=lambda r: (r.processed, r.id), reverse=True)
    start = (page - 1) * page_size
    if page > 1 and start >= len(rows):
        raise beyond_last_page(page)
    shown = rows[start : start + page_size]
    return ProcessedMailPage(
        count=len(rows),
        next_page=page + 1 if start + page_size < len(rows) else None,
        mails=tuple(
            ProcessedMail(
                id=r.id,
                rule_id=r.rule,
                folder=r.folder,
                subject=r.subject,
                received=r.received,
                processed=r.processed,
                status=r.status,
                error=r.error,
            )
            for r in shown
        ),
    )


def create_account(data: MailData, user: Viewer, values: MailAccountValues) -> MailAccount:
    """`MailWriter.create_mail_account` (MailAccountViewSet create)."""
    _allow(user, "add", "mailaccount")
    sent = values.model_fields_set
    for name in ("name", "imap_server", "imap_port", "username", "password"):
        if name not in sent or getattr(values, name) is None:
            reject(name, "This field is required.")
    assert values.name is not None
    assert values.imap_server is not None
    assert values.imap_port is not None
    assert values.username is not None
    assert values.password is not None
    secret = values.password.get_secret_value()
    check_password(secret)
    for name, text in (("name", values.name), ("username", values.username), ("password", secret)):
        if not text:
            reject(name, "This field may not be blank.")
    if any(r.name == values.name for r in data.accounts):
        reject("name", "mail account with this name already exists.")
    row = FakeMailAccount(
        id=max((r.id for r in data.accounts), default=0) + 1,
        name=values.name,
        imap_server=values.imap_server,
        imap_port=values.imap_port,
        username=values.username,
        password=secret,
        owner=user.id,
    )
    if values.imap_security is not None:
        row.imap_security = values.imap_security
    if values.character_set is not None:
        row.character_set = values.character_set
    data.accounts.append(row)
    return _account(row, user)


def update_account(
    data: MailData, user: Viewer, account_id: int, values: MailAccountValues
) -> MailAccount:
    """`MailWriter.update_mail_account` (MailAccountViewSet partial_update)."""
    _allow(user, "change", "mailaccount")
    row = _own_account(data, user, account_id)
    if not _may_change(row.owner, user):
        raise denied()
    if values.password is not None:
        check_password(values.password.get_secret_value())
    if values.name is not None and any(
        r.name == values.name and r.id != account_id for r in data.accounts
    ):
        reject("name", "mail account with this name already exists.")
    given: dict[str, object] = {
        "name": values.name,
        "imap_server": values.imap_server,
        "imap_port": values.imap_port,
        "imap_security": values.imap_security,
        "username": values.username,
        "character_set": values.character_set,
    }
    changes = {name: value for name, value in given.items() if value is not None}
    if values.password is not None:
        changes["password"] = values.password.get_secret_value()
    for name, value in changes.items():
        if value == "":
            reject(name, "This field may not be blank.")
    index = data.accounts.index(row)
    data.accounts[index] = replace(row, **changes)
    return _account(data.accounts[index], user)


def delete_account(data: MailData, user: Viewer, account_id: int) -> None:
    """`MailWriter.delete_mail_account` (MailAccountViewSet destroy); rules cascade."""
    _allow(user, "delete", "mailaccount")
    row = _own_account(data, user, account_id)
    if not _may_change(row.owner, user):
        raise denied()
    gone = {r.id for r in data.rules if r.account == account_id}
    data.accounts.remove(row)
    data.rules[:] = [r for r in data.rules if r.account != account_id]
    data.processed[:] = [m for m in data.processed if m.rule not in gone]


def test_account(data: MailData, user: Viewer, account_id: int) -> MailAccountTestResult:
    """`MailWriter.test_mail_account` (MailAccountViewSet test, with the saved password)."""
    row = _own_account(data, user, account_id)
    if not user.has("change_mailaccount") or not _may_change(row.owner, user):
        raise denied()
    if row.imap_server in data.silent_hosts:
        return silent_server()
    if row.imap_server not in data.hosts:
        return unreachable_server()
    if (row.imap_server, row.imap_port, row.username, row.password) not in data.logins:
        return failed_login()
    return connected()


def process_account(data: MailData, user: Viewer, account_id: int) -> None:
    """`MailWriter.process_mail_account` (MailAccountViewSet process): queued, nothing fetched."""
    row = _own_account(data, user, account_id)
    _allow(user, "add", "mailaccount")  # a POST: DRF asks for the add permission
    if not _may_change(row.owner, user):
        raise denied()
    data.fetches.append(row.id)


def _exists(kind: str, ids: Collection[int | None], known: frozenset[int]) -> None:
    for number in ids:
        if number is not None and number not in known:
            reject(kind, f'Invalid pk "{number}" - object does not exist.')


def _check_account(user: Viewer, data: MailData, account_id: int) -> None:
    """The rule's account must exist, be visible and be one the user may change."""
    found = [r for r in data.accounts if r.id == account_id and _visible(r.owner, user)]
    if not found:
        reject("account", f'Invalid pk "{account_id}" - object does not exist.')
    if not user.has("change_mailaccount") or not _may_change(found[0].owner, user):
        message = "Insufficient permissions."
        raise PermissionDenied(message)


def _check_numbers_and_action(values: MailRuleValues) -> None:
    if values.maximum_age is not None:
        if values.maximum_age < 0:
            reject("maximum_age", "Ensure this value is greater than or equal to 0.")
        if values.maximum_age > MAX_AGE:
            reject("maximum_age", "Maximum mail age is unreasonably large.")
    if "action_parameter" in values.model_fields_set and values.action_parameter == "":
        reject("action_parameter", "This field may not be blank.")
    if values.action in {"move", "tag"} and not values.action_parameter:
        message = "Paperless rejected the request: An action parameter is required."
        raise Validation(message)


def _rule_values(
    user: Viewer, data: MailData, values: MailRuleValues, *, creating: bool
) -> dict[str, object]:
    """The checked changes of `values` as row attributes (the serializer's validation)."""
    sent = values.model_fields_set
    changes: dict[str, object] = {
        ("account" if name == "account_id" else name): getattr(values, name) for name in sent
    }
    if changes.get("assign_tags", ()) is None:
        del changes["assign_tags"]
    if creating and values.name is None:
        reject("name", "This field is required.")
    if creating and values.account_id is None:
        reject("account", "This field is required.")
    for name in ("name", "folder"):
        if name in sent and getattr(values, name) == "":
            reject(name, "This field may not be blank.")
    if values.account_id is not None:
        _check_account(user, data, values.account_id)
    _exists("assign_tags", values.assign_tags or (), user.tags)
    _exists("assign_correspondent", [values.assign_correspondent], user.correspondents)
    _exists("assign_document_type", [values.assign_document_type], user.document_types)
    _exists("owner", [values.owner], user.users)
    _check_numbers_and_action(values)
    return changes


def create_rule(data: MailData, user: Viewer, values: MailRuleValues) -> MailRule:
    """`MailWriter.create_mail_rule` (MailRuleViewSet create)."""
    _allow(user, "add", "mailrule")
    changes = _rule_values(user, data, values, creating=True)
    if any(r.name == values.name and r.owner == user.id for r in data.rules):
        reject("error", "Object violates owner / name unique constraint")
    assert values.name is not None
    assert values.account_id is not None
    _ = changes.setdefault("owner", user.id)
    row = FakeMailRule(
        id=max((r.id for r in data.rules), default=0) + 1,
        name=values.name,
        account=values.account_id,
    )
    row = replace(row, **{k: v for k, v in changes.items() if k not in {"name", "account"}})
    data.rules.append(row)
    return _rule(row, user)


def update_rule(data: MailData, user: Viewer, rule_id: int, values: MailRuleValues) -> MailRule:
    """`MailWriter.update_mail_rule` (MailRuleViewSet partial_update)."""
    _allow(user, "change", "mailrule")
    row = _own_rule(data, user, rule_id)
    if not _may_change(row.owner, user):
        raise denied()
    changes = _rule_values(user, data, values, creating=False)
    index = data.rules.index(row)
    data.rules[index] = replace(row, **changes)
    return _rule(data.rules[index], user)


def delete_rule(data: MailData, user: Viewer, rule_id: int) -> None:
    """`MailWriter.delete_mail_rule` (MailRuleViewSet destroy); processed mail cascades."""
    _allow(user, "delete", "mailrule")
    row = _own_rule(data, user, rule_id)
    if not _may_change(row.owner, user):
        raise denied()
    data.rules.remove(row)
    data.processed[:] = [m for m in data.processed if m.rule != rule_id]
