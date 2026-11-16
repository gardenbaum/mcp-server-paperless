"""MailReader and MailWriter on the mail account, rule and processed mail endpoints.

The paths are ``/api/mail_accounts/``, ``/api/mail_rules/`` and ``/api/processed_mail/``. Probed on
3.4.2:

- Paperless masks the password in every answer with ``*`` repeated ``max(10, length)`` times
  (so the mask still tells the length of a long password). The row models here have no password
  field, so neither the password nor its mask gets any further than the parse.
- A password that consists of ``*`` only means "unchanged" on a PATCH and is ignored: a real
  password of that kind would be dropped without a word, so it is refused up front.
- ``POST /api/mail_accounts/test/`` takes an ``id`` and a password of ``*`` to test the saved
  password. A failed login is a 400 (text), a refused connection or an unknown host a 500, and a
  server that accepts the connection but never answers holds the request for minutes, so the
  request has a timeout of its own and nothing is retried. An unknown ``id`` is a 403.
- ``POST /api/mail_accounts/{id}/process/`` queues the fetch and answers ``{"result": "OK"}`` at
  once. The schema asks for an account as its body but the view ignores it; a body made of the
  saved account with the ``*`` password is sent to satisfy the schema.
- A rule whose action is move or tag needs an ``action_parameter`` (400). Changing the action
  leaves the old parameter in place. Blank filters are stored as ``""``; ``null`` clears them.
- Deleting an account deletes its rules, and deleting a rule its processed mail (cascade).
"""

from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel

from paperless_mcp.domain.errors import (
    NotFound,
    PaperlessMcpError,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
)
from paperless_mcp.domain.mail import (
    AccountType,
    AttachmentScope,
    CorrespondentSource,
    ImapSecurity,
    MailAccount,
    MailAccountTestResult,
    MailAccountValues,
    MailRule,
    MailRuleValues,
    MailScope,
    PdfLayout,
    ProcessedMail,
    ProcessedMailPage,
    ProcessedStatus,
    RuleAction,
    TitleSource,
    check_password,
)
from paperless_mcp.paperless.pagination import MAX_ITEMS, Page, collect

if TYPE_CHECKING:
    from collections.abc import Mapping

    from paperless_mcp.paperless.client import PaperlessClient

ACCOUNTS: Final = "/api/mail_accounts/"
RULES: Final = "/api/mail_rules/"
PROCESSED: Final = "/api/processed_mail/"
TEST: Final = f"{ACCOUNTS}test/"
# What Paperless takes as "keep the saved password" (anything made of asterisks only).
KEEP_SAVED: Final = "*"
# A login that works takes seconds; a server that never answers would hold Paperless for minutes.
TEST_TIMEOUT: Final = 45.0

SECURITY: Final[dict[int, ImapSecurity]] = {1: "none", 2: "ssl", 3: "starttls"}
ACCOUNT_TYPE: Final[dict[int, AccountType]] = {1: "imap", 2: "gmail_oauth", 3: "outlook_oauth"}
ACTION: Final[dict[int, RuleAction]] = {
    1: "delete",
    2: "move",
    3: "mark_read",
    4: "flag",
    5: "tag",
}
TITLE: Final[dict[int, TitleSource]] = {1: "subject", 2: "filename", 3: "none"}
CORRESPONDENT: Final[dict[int, CorrespondentSource]] = {
    1: "none",
    2: "email_address",
    3: "name",
    4: "selected",
}
ATTACHMENT: Final[dict[int, AttachmentScope]] = {1: "attachments_only", 2: "all_files"}
SCOPE: Final[dict[int, MailScope]] = {1: "attachments", 2: "eml", 3: "eml_and_attachments"}
LAYOUT: Final[dict[int, PdfLayout]] = {
    0: "default",
    1: "text_html",
    2: "html_text",
    3: "html",
    4: "text",
}


def _code(names: Mapping[int, str], name: str) -> int:
    """The wire number of ``name``."""
    return next(number for number, candidate in names.items() if candidate == name)


class _AccountRow(BaseModel):
    """An account as Paperless sends it. The ``password`` it also sends is not read at all."""

    id: int
    name: str
    imap_server: str
    imap_port: int
    imap_security: Literal[1, 2, 3]
    username: str
    character_set: str
    is_token: bool
    owner: int | None
    user_can_change: bool
    account_type: Literal[1, 2, 3]
    expiration: datetime | None


class _RuleRow(BaseModel):
    id: int
    name: str
    account: int
    enabled: bool
    order: int
    folder: str
    filter_from: str | None
    filter_to: str | None
    filter_subject: str | None
    filter_body: str | None
    filter_attachment_filename_include: str | None
    filter_attachment_filename_exclude: str | None
    maximum_age: int
    attachment_type: Literal[1, 2]
    consumption_scope: Literal[1, 2, 3]
    pdf_layout: Literal[0, 1, 2, 3, 4]
    action: Literal[1, 2, 3, 4, 5]
    action_parameter: str | None
    assign_title_from: Literal[1, 2, 3]
    assign_tags: list[int | None]
    assign_correspondent_from: Literal[1, 2, 3, 4]
    assign_correspondent: int | None
    assign_document_type: int | None
    assign_owner_from_rule: bool
    stop_processing: bool
    owner: int | None
    user_can_change: bool


class _ProcessedRow(BaseModel):
    id: int
    rule: int
    folder: str
    subject: str
    received: datetime
    processed: datetime
    status: str
    error: str | None


class _Tested(BaseModel):
    success: bool


class _Done(BaseModel):
    result: str | None = None


def unseen_account(account_id: int) -> NotFound:
    """The error for a mail account that does not exist or that the user may not see."""
    message = f"No mail account {account_id} exists that this user may see."
    return NotFound(message, hint="Call list_mail_accounts to see the accounts you can use.")


def unseen_rule(rule_id: int) -> NotFound:
    """The error for a mail rule that does not exist or that the user may not see."""
    message = f"No mail rule {rule_id} exists that this user may see."
    return NotFound(message, hint="Call list_mail_rules to see the rules you can use.")


def beyond_last_page(page: int) -> NotFound:
    """The error for a page past the end of the processed mail."""
    return NotFound(
        f"Page {page} of the processed mail does not exist.",
        hint="List it with page=1; count and next_page tell how far it goes.",
    )


def failed_login() -> MailAccountTestResult:
    """The result of a test whose login failed: Paperless says no more than that (HTTP 400)."""
    return MailAccountTestResult(
        success=False,
        message=(
            "Paperless could not connect to the mail server or log in. Check the server name, "
            "port, security setting, username and password."
        ),
    )


def unreachable_server() -> MailAccountTestResult:
    """The result of a test that could not connect, or that Paperless failed on (HTTP 500)."""
    return MailAccountTestResult(
        success=False,
        message=(
            "Paperless could not connect to the mail server, or failed while trying (the host "
            "is unknown or refuses the connection, or the server broke off). Check the server "
            "name, port and security setting."
        ),
    )


def silent_server() -> MailAccountTestResult:
    """The result of a test that got no answer in time."""
    return MailAccountTestResult(
        success=False,
        message=(
            f"The mail server did not answer within {TEST_TIMEOUT:.0f} seconds. Check the port "
            "and the security setting: a port that expects TLS never answers a plain "
            "connection. The Paperless web worker may stay busy for several minutes, so do not "
            "try again at once."
        ),
    )


def connected() -> MailAccountTestResult:
    """The result of a test that connected and logged in."""
    return MailAccountTestResult(success=True, message="Paperless connected and logged in.")


def _account(row: _AccountRow) -> MailAccount:
    return MailAccount(
        id=row.id,
        name=row.name,
        imap_server=row.imap_server,
        imap_port=row.imap_port,
        imap_security=SECURITY[row.imap_security],
        username=row.username,
        character_set=row.character_set,
        account_type=ACCOUNT_TYPE[row.account_type],
        is_token=row.is_token,
        owner=row.owner,
        can_change=row.user_can_change,
        expiration=row.expiration,
    )


def _rule(row: _RuleRow) -> MailRule:
    return MailRule(
        id=row.id,
        name=row.name,
        account_id=row.account,
        enabled=row.enabled,
        order=row.order,
        folder=row.folder,
        filter_from=row.filter_from or None,
        filter_to=row.filter_to or None,
        filter_subject=row.filter_subject or None,
        filter_body=row.filter_body or None,
        filter_attachment_filename_include=row.filter_attachment_filename_include or None,
        filter_attachment_filename_exclude=row.filter_attachment_filename_exclude or None,
        maximum_age=row.maximum_age,
        attachment_type=ATTACHMENT[row.attachment_type],
        consumption_scope=SCOPE[row.consumption_scope],
        pdf_layout=LAYOUT[row.pdf_layout],
        action=ACTION[row.action],
        action_parameter=row.action_parameter or None,
        assign_title_from=TITLE[row.assign_title_from],
        assign_tags=tuple(number for number in row.assign_tags if number is not None),
        assign_correspondent_from=CORRESPONDENT[row.assign_correspondent_from],
        assign_correspondent=row.assign_correspondent,
        assign_document_type=row.assign_document_type,
        assign_owner_from_rule=row.assign_owner_from_rule,
        stop_processing=row.stop_processing,
        owner=row.owner,
        can_change=row.user_can_change,
    )


def _processed(row: _ProcessedRow) -> ProcessedMail:
    return ProcessedMail(
        id=row.id,
        rule_id=row.rule,
        folder=row.folder,
        subject=row.subject,
        received=row.received,
        processed=row.processed,
        status=row.status,
        error=row.error or None,
    )


def _account_body(values: MailAccountValues) -> dict[str, object]:
    """The request body of the fields set in ``values``; the only place a password is unwrapped."""
    sent = values.model_fields_set
    plain: tuple[tuple[str, object], ...] = (
        ("name", values.name),
        ("imap_server", values.imap_server),
        ("imap_port", values.imap_port),
        ("username", values.username),
        ("character_set", values.character_set),
    )
    body: dict[str, object] = {
        name: value for name, value in plain if name in sent and value is not None
    }
    if "imap_security" in sent and values.imap_security is not None:
        body["imap_security"] = _code(SECURITY, values.imap_security)
    if "password" in sent and values.password is not None:
        secret = values.password.get_secret_value()
        check_password(secret)
        body["password"] = secret
    return body


def _rule_body(values: MailRuleValues) -> dict[str, object]:
    """The request body of the fields set in ``values``."""
    sent = values.model_fields_set
    body: dict[str, object] = {}
    plain = (
        *("name", "enabled", "order", "folder", "maximum_age", "assign_owner_from_rule"),
        *("stop_processing", "owner"),
        *("assign_correspondent", "assign_document_type", "action_parameter"),
        *("filter_from", "filter_to", "filter_subject", "filter_body"),
        *("filter_attachment_filename_include", "filter_attachment_filename_exclude"),
    )
    for name in plain:
        if name in sent:
            body[name] = getattr(values, name)
    if "account_id" in sent:
        body["account"] = values.account_id
    coded = (
        ("attachment_type", ATTACHMENT, values.attachment_type),
        ("consumption_scope", SCOPE, values.consumption_scope),
        ("pdf_layout", LAYOUT, values.pdf_layout),
        ("action", ACTION, values.action),
        ("assign_title_from", TITLE, values.assign_title_from),
        ("assign_correspondent_from", CORRESPONDENT, values.assign_correspondent_from),
    )
    for name, names, value in coded:
        if name in sent and value is not None:
            body[name] = _code(names, value)
    if "assign_tags" in sent and values.assign_tags is not None:
        body["assign_tags"] = list(values.assign_tags)
    return body


class PaperlessMailReader:
    """``MailReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def list_mail_accounts(self) -> tuple[MailAccount, ...]:
        """See ``MailReader.list_mail_accounts``."""
        collected = await collect(self._client, ACCOUNTS, Page[_AccountRow])
        if collected.truncated:
            message = (
                f"Paperless has more than {MAX_ITEMS} mail accounts; this server reads up to that."
            )
            raise PaperlessMcpError(message)
        return tuple(_account(row) for row in collected.items)

    async def get_mail_account(self, account_id: int) -> MailAccount:
        """See ``MailReader.get_mail_account``."""
        try:
            return _account(await self._client.get(f"{ACCOUNTS}{account_id}/", _AccountRow))
        except NotFound:
            raise unseen_account(account_id) from None

    async def list_mail_rules(self) -> tuple[MailRule, ...]:
        """See ``MailReader.list_mail_rules``."""
        collected = await collect(self._client, RULES, Page[_RuleRow])
        if collected.truncated:
            message = (
                f"Paperless has more than {MAX_ITEMS} mail rules; this server reads up to that."
            )
            raise PaperlessMcpError(message)
        return tuple(_rule(row) for row in collected.items)

    async def get_mail_rule(self, rule_id: int) -> MailRule:
        """See ``MailReader.get_mail_rule``."""
        try:
            return _rule(await self._client.get(f"{RULES}{rule_id}/", _RuleRow))
        except NotFound:
            raise unseen_rule(rule_id) from None

    async def list_processed_mail(
        self, *, rule_id: int | None, status: ProcessedStatus | None, page: int, page_size: int
    ) -> ProcessedMailPage:
        """See ``MailReader.list_processed_mail``."""
        params: dict[str, str | int] = {
            "page": page,
            "page_size": page_size,
            "ordering": "-processed",
        }
        if rule_id is not None:
            params["rule"] = rule_id
        if status is not None:
            params["status"] = status
        try:
            found = await self._client.get(PROCESSED, Page[_ProcessedRow], params=params)
        except NotFound:
            raise beyond_last_page(page) from None
        return ProcessedMailPage(
            count=found.count,
            next_page=found.next_page,
            mails=tuple(_processed(row) for row in found.results),
        )


class PaperlessMailWriter:
    """``MailWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def _row(self, account_id: int) -> _AccountRow:
        try:
            return await self._client.get(f"{ACCOUNTS}{account_id}/", _AccountRow)
        except NotFound:
            raise unseen_account(account_id) from None

    async def create_mail_account(self, values: MailAccountValues) -> MailAccount:
        """See ``MailWriter.create_mail_account``."""
        body = _account_body(values)
        return _account(await self._client.call("POST", ACCOUNTS, _AccountRow, json=body))

    async def update_mail_account(self, account_id: int, values: MailAccountValues) -> MailAccount:
        """See ``MailWriter.update_mail_account``."""
        body = _account_body(values)
        try:
            row = await self._client.call(
                "PATCH", f"{ACCOUNTS}{account_id}/", _AccountRow, json=body
            )
        except NotFound:
            raise unseen_account(account_id) from None
        return _account(row)

    async def delete_mail_account(self, account_id: int) -> None:
        """See ``MailWriter.delete_mail_account``."""
        try:
            _ = await self._client.request("DELETE", f"{ACCOUNTS}{account_id}/", retry=False)
        except NotFound:
            raise unseen_account(account_id) from None

    async def _saved(self, account_id: int) -> dict[str, object]:
        """The saved account as a request body that keeps its password (the schema needs one)."""
        row = await self._row(account_id)
        return {
            "name": row.name,
            "imap_server": row.imap_server,
            "imap_port": row.imap_port,
            "imap_security": row.imap_security,
            "username": row.username,
            "character_set": row.character_set,
            "is_token": row.is_token,
            "password": KEEP_SAVED,
        }

    async def test_mail_account(self, account_id: int) -> MailAccountTestResult:
        """See ``MailWriter.test_mail_account``: a failed connection is a result, not an error."""
        body = await self._saved(account_id) | {"id": account_id}
        try:
            _ = await self._client.call(
                "POST", TEST, _Tested, json=body, timeout=TEST_TIMEOUT, retry=False
            )
        except Validation:
            return failed_login()
        except UpstreamTimeout:
            return silent_server()
        except UpstreamUnreachable:
            raise
        except UpstreamUnavailable as error:
            if error.status == 500:  # noqa: PLR2004 -- Paperless answers 500 for a refused connection
                return unreachable_server()
            raise
        return connected()

    async def process_mail_account(self, account_id: int) -> None:
        """See ``MailWriter.process_mail_account``: queued at once, never repeated."""
        body = await self._saved(account_id)
        try:
            _ = await self._client.call(
                "POST", f"{ACCOUNTS}{account_id}/process/", _Done, json=body, retry=False
            )
        except NotFound:
            raise unseen_account(account_id) from None

    async def create_mail_rule(self, values: MailRuleValues) -> MailRule:
        """See ``MailWriter.create_mail_rule``."""
        body = _rule_body(values)
        return _rule(await self._client.call("POST", RULES, _RuleRow, json=body))

    async def update_mail_rule(self, rule_id: int, values: MailRuleValues) -> MailRule:
        """See ``MailWriter.update_mail_rule``."""
        body = _rule_body(values)
        try:
            row = await self._client.call("PATCH", f"{RULES}{rule_id}/", _RuleRow, json=body)
        except NotFound:
            raise unseen_rule(rule_id) from None
        return _rule(row)

    async def delete_mail_rule(self, rule_id: int) -> None:
        """See ``MailWriter.delete_mail_rule``."""
        try:
            _ = await self._client.request("DELETE", f"{RULES}{rule_id}/", retry=False)
        except NotFound:
            raise unseen_rule(rule_id) from None
