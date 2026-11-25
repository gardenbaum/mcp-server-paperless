"""Mail import: accounts, rules and processed mail (opt-in ``mail`` toolset).

Paperless fetches mail from IMAP accounts every ten minutes. A rule says which mails of an account
to look at (folder, age, filters), which metadata the documents made from their attachments get,
and what to do with a mail afterwards on the mail server: mark it read, flag it, tag it, move it
or delete it. Paperless remembers every mail it handled (processed mail) so none is imported twice.

Credentials never flow back to the client (design §6, §7): a mail account here has no password
field at all. A password only goes in, as a ``SecretStr`` whose representation hides it; Paperless
masks it in its answers and this server drops that mask before a row is built.
"""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from mcp_server_paperless.domain.documents import NameOrId
from mcp_server_paperless.domain.errors import Validation

type ImapSecurity = Literal["none", "ssl", "starttls"]
type AccountType = Literal["imap", "gmail_oauth", "outlook_oauth"]
type RuleAction = Literal["delete", "move", "mark_read", "flag", "tag"]
type TitleSource = Literal["subject", "filename", "none"]
type CorrespondentSource = Literal["none", "email_address", "name", "selected"]
type AttachmentScope = Literal["attachments_only", "all_files"]
type MailScope = Literal["attachments", "eml", "eml_and_attachments"]
type PdfLayout = Literal["default", "text_html", "html_text", "html", "text"]
type ProcessedStatus = Literal["SUCCESS", "FAILED", "PROCESSED_WO_CONSUMPTION"]
type ProcessedOrdering = Literal["-processed", "processed", "-received", "received"]
# Rule settings that update_mail_rule can empty; the filters and the action parameter are emptied
# by passing an empty string.
type RuleClearable = Literal["assign_correspondent", "assign_document_type", "owner"]

# Actions that need a folder name or a keyword in ``action_parameter``.
ACTIONS_WITH_PARAMETER: Final = frozenset[RuleAction]({"move", "tag"})
# Actions that remove or relocate mails on the mail server.
ACTIONS_REMOVING_MAIL: Final = frozenset[RuleAction]({"delete", "move"})


def masked_password() -> Validation:
    """The error for a password made of asterisks only, which Paperless would not store."""
    message = (
        "A password that consists only of asterisks cannot be saved: Paperless takes that to "
        "mean 'keep the saved password'."
    )
    return Validation(message, hint="Leave password out to keep the saved one.")


def check_password(password: str | None) -> None:
    """Refuse a password that Paperless would ignore as 'unchanged'.

    Raises:
        Validation: ``password`` is not empty and made of asterisks only.
    """
    if password is not None and password and not password.strip("*"):
        raise masked_password()


def token_account() -> Validation:
    """The error for a new password of an account that signs in with an OAuth access token."""
    message = (
        "The mail account signs in with an OAuth access token, which is not replaced here. "
        "Nothing was changed."
    )
    hint = (
        "Set the account up again in the Paperless web UI; its other settings can be changed here."
    )
    return Validation(message, hint=hint)


class NotText(SecretStr):
    """Stands in for a password that was not text; the service refuses it without quoting it."""

    def __init__(self) -> None:
        """Hold nothing."""
        super().__init__("")


class MailAccount(BaseModel):
    """A mail account as the port reads it. It has no password: nothing here can leak one."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    imap_server: str
    imap_port: int
    imap_security: ImapSecurity
    username: str
    character_set: str
    account_type: AccountType
    is_token: bool
    owner: int | None
    can_change: bool
    expiration: datetime | None


class MailAccountValues(BaseModel):
    """What to write to a mail account; only the fields that are set (``model_fields_set``) go.

    The password is write-only: a ``SecretStr``, shown as ``**********`` by every repr.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    imap_server: str | None = None
    imap_port: int | None = None
    imap_security: ImapSecurity | None = None
    username: str | None = None
    password: SecretStr | None = None
    character_set: str | None = None


class MailAccountTestResult(BaseModel):
    """The outcome of a connection test, as the port reports it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    success: bool
    message: str


class MailRule(BaseModel):
    """A mail rule as the port reads it, with taxonomy as ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    account_id: int
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
    attachment_type: AttachmentScope
    consumption_scope: MailScope
    pdf_layout: PdfLayout
    action: RuleAction
    action_parameter: str | None
    assign_title_from: TitleSource
    assign_tags: tuple[int, ...]
    assign_correspondent_from: CorrespondentSource
    assign_correspondent: int | None
    assign_document_type: int | None
    assign_owner_from_rule: bool
    stop_processing: bool
    owner: int | None
    can_change: bool


class MailRuleValues(BaseModel):
    """What to write to a mail rule; only the fields that are set (``model_fields_set``) go.

    ``None`` is a value for the nullable fields (filters, action parameter, correspondent,
    document type, owner): it clears them.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    account_id: int | None = None
    enabled: bool | None = None
    order: int | None = None
    folder: str | None = None
    filter_from: str | None = None
    filter_to: str | None = None
    filter_subject: str | None = None
    filter_body: str | None = None
    filter_attachment_filename_include: str | None = None
    filter_attachment_filename_exclude: str | None = None
    maximum_age: int | None = None
    attachment_type: AttachmentScope | None = None
    consumption_scope: MailScope | None = None
    pdf_layout: PdfLayout | None = None
    action: RuleAction | None = None
    action_parameter: str | None = None
    assign_title_from: TitleSource | None = None
    assign_tags: tuple[int, ...] | None = None
    assign_correspondent_from: CorrespondentSource | None = None
    assign_correspondent: int | None = None
    assign_document_type: int | None = None
    assign_owner_from_rule: bool | None = None
    stop_processing: bool | None = None
    owner: int | None = None


class MailRuleSettings(BaseModel):
    """The settings a caller gives for a rule, taxonomy by name or id; ``None`` means not given.

    An empty string empties a filter or the action parameter. To empty the correspondent, the
    document type or the owner, the caller names them in ``clear``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    account_id: int | None = None
    enabled: bool | None = None
    order: int | None = None
    folder: str | None = None
    filter_from: str | None = None
    filter_to: str | None = None
    filter_subject: str | None = None
    filter_body: str | None = None
    filter_attachment_filename_include: str | None = None
    filter_attachment_filename_exclude: str | None = None
    maximum_age_days: int | None = None
    attachment_type: AttachmentScope | None = None
    consumption_scope: MailScope | None = None
    pdf_layout: PdfLayout | None = None
    action: RuleAction | None = None
    action_parameter: str | None = None
    assign_title_from: TitleSource | None = None
    assign_tags: tuple[NameOrId, ...] | None = None
    assign_correspondent_from: CorrespondentSource | None = None
    assign_correspondent: NameOrId | None = None
    assign_document_type: NameOrId | None = None
    assign_owner_from_rule: bool | None = None
    stop_processing: bool | None = None
    owner: NameOrId | None = None


class ProcessedMail(BaseModel):
    """A mail Paperless handled, as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    owner: int | None
    rule_id: int
    folder: str
    uid: str
    subject: str
    received: datetime
    processed: datetime
    status: str
    error: str | None


class ProcessedMailPage(BaseModel):
    """One page of processed mail as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    mails: tuple[ProcessedMail, ...]


# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class MailAccountInfo(BaseModel):
    """A mail account. The password is never returned, only accepted when it is set."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    imap_server: str
    imap_port: int
    imap_security: ImapSecurity
    username: str
    character_set: str
    account_type: Annotated[
        AccountType,
        Field(
            description="imap: password login; gmail_oauth, outlook_oauth: set up in the web UI."
        ),
    ]
    is_token: Annotated[
        bool, Field(description="The stored secret is an OAuth access token, not a password.")
    ]
    owner: Annotated[int | None, Field(description="Id of the owning user; null: shared.")]
    can_change: Annotated[bool, Field(description="Whether this user may change the account.")]
    token_expires: Annotated[
        datetime | None,
        Field(
            description=(
                "When the access token expires: Paperless renews it at its next use (the refresh "
                "token is never in the API). Null: not applicable."
            )
        ),
    ]


class MailAccountListing(BaseModel):
    """One page of mail accounts."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many accounts there are in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    accounts: tuple[MailAccountInfo, ...]


class MailAccountDeletion(BaseModel):
    """The outcome of a confirmed delete of a mail account."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    message: Annotated[str, Field(description="What was deleted.")]


class MailAccountTested(BaseModel):
    """The outcome of a connection test of a saved mail account."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    success: Annotated[bool, Field(description="Paperless connected and logged in.")]
    message: Annotated[str, Field(description="What happened and what to check if it failed.")]


class MailFetchStarted(BaseModel):
    """The outcome of process_mail_account: Paperless queued the fetch."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    message: Annotated[str, Field(description="What was started and where to see the outcome.")]


class Reference(BaseModel):
    """A tag, correspondent, document type or user a rule assigns, with its name if visible."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: Annotated[str | None, Field(description="Null if the user may not see its name.")]


class MailRuleInfo(BaseModel):
    """A mail rule: which mails of an account to import, and what happens to them afterwards."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    account_id: int
    enabled: bool
    order: Annotated[int, Field(description="Rules run in this order, lowest first.")]
    folder: str
    filter_from: str | None
    filter_to: str | None
    filter_subject: str | None
    filter_body: str | None
    filter_attachment_filename_include: str | None
    filter_attachment_filename_exclude: str | None
    maximum_age_days: Annotated[int, Field(description="Only mails up to this many days old.")]
    attachment_type: AttachmentScope
    consumption_scope: MailScope
    pdf_layout: PdfLayout
    action: Annotated[
        RuleAction,
        Field(description="What happens to a mail on the mail server after documents were made."),
    ]
    action_parameter: Annotated[
        str | None, Field(description="The folder for move, the keyword for tag.")
    ]
    assign_title_from: TitleSource
    assign_tags: tuple[Reference, ...]
    assign_correspondent_from: CorrespondentSource
    assign_correspondent: Annotated[
        Reference | None, Field(description="Used with assign_correspondent_from selected.")
    ]
    assign_document_type: Reference | None
    assign_owner_from_rule: Annotated[
        bool, Field(description="Documents get the owner of the rule.")
    ]
    stop_processing: Annotated[
        bool,
        Field(description="No later rule runs once this or an earlier one queued a document."),
    ]
    owner: Reference | None
    can_change: bool


class MailRuleListing(BaseModel):
    """One page of mail rules."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many rules match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    rules: tuple[MailRuleInfo, ...]


class MailRuleDeletion(BaseModel):
    """The outcome of a confirmed delete of a mail rule."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    message: Annotated[str, Field(description="What was deleted.")]


STATUS_HELP: Final = (
    "SUCCESS, FAILED (the rule's action did not run) or PROCESSED_WO_CONSUMPTION (nothing to "
    "import, no action)."
)


class ProcessedMailRow(BaseModel):
    """A mail Paperless handled: when, from which rule, and how it went."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    rule_id: int
    subject: str
    folder: str
    uid: Annotated[str, Field(description="The mail's number in the folder on the mail server.")]
    received: datetime
    processed: datetime
    status: Annotated[str, Field(description=STATUS_HELP)]
    error: Annotated[str | None, Field(description="Why it failed, cut to one short line.")]


class ProcessedMailListing(BaseModel):
    """One page of processed mail."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many processed mails match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    mails: tuple[ProcessedMailRow, ...]


class ProcessedMailDetail(BaseModel):
    """One mail Paperless handled, with a part of its error. Subject and error are data."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    rule: Annotated[Reference, Field(description="The rule it was handled by.")]
    folder: str
    uid: Annotated[str, Field(description="The mail's number in the folder on the mail server.")]
    subject: str
    received: datetime
    processed: datetime
    status: Annotated[str, Field(description=STATUS_HELP)]
    owner: Annotated[int | None, Field(description="Id of the owning user; null: shared.")]
    error: Annotated[str | None, Field(description="The error from error_from on.")]
    error_from: int
    error_total: Annotated[int, Field(description="Length of the whole error in characters.")]
    error_next: Annotated[
        int | None, Field(description="Pass as error_from to read on; null at the end.")
    ]
    refetch: Annotated[
        str, Field(description="Whether and when the mail is fetched again without this record.")
    ]


type RecordOutcome = Literal["would_delete", "deleted", "still_there", "unknown"]


class DeletedRecord(BaseModel):
    """A record of processed mail that was to be deleted."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    rule_id: int
    subject: Annotated[str, Field(description="Cut to 60 characters.")]
    status: str
    outcome: Annotated[
        RecordOutcome,
        Field(description="unknown: what happened to it could not be read afterwards."),
    ]


class ProcessedMailDeletion(BaseModel):
    """What became of records of processed mail, or what would."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    dry_run: bool
    mails: tuple[DeletedRecord, ...]
    message: Annotated[
        str, Field(description="What was deleted; in a preview each mail and what follows.")
    ]


type CheckResult = Literal["ok", "no", "unknown"]


class MailFacts(BaseModel):
    """What the caller knows about a mail; null: not known."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    received: datetime | None = None
    folder: str | None = None
    attachment_names: tuple[str, ...] | None = None
    is_read: bool | None = None
    is_flagged: bool | None = None


class MailCheck(BaseModel):
    """One thing that decides whether a rule takes a mail."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    what: str
    result: Annotated[
        CheckResult, Field(description="no: it most likely keeps the mail out; never certain.")
    ]
    why: str


class RuleChecks(BaseModel):
    """What can be said of one rule and the mail."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    rule_id: int
    name: str
    result: Annotated[
        CheckResult,
        Field(description="no: a check says no; ok: nothing found against it, no more than that."),
    ]
    checks: tuple[MailCheck, ...]


class ExplainedRecord(BaseModel):
    """A record of processed mail with the subject of the mail."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    rule_id: int
    status: Annotated[str, Field(description=STATUS_HELP)]
    processed: datetime
    error: Annotated[str | None, Field(description="One short line; get_processed_mail has all.")]


class ExplainedTask(BaseModel):
    """A mail fetch, or a file from a mail that could not be consumed."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_type: str
    state: str
    created: datetime
    message: str | None


class MailExplanation(BaseModel):
    """Why a mail may not have been imported, from what Paperless has stored. No dry run."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    message: str
    account: tuple[str, ...]
    rules: tuple[RuleChecks, ...]
    processed: tuple[ExplainedRecord, ...]
    tasks: tuple[ExplainedTask, ...]
    logs: Annotated[
        tuple[str, ...], Field(description="Log lines about the account and its rules: data.")
    ]
    logs_from: Annotated[
        str | None, Field(description="The oldest log line that was looked at, in Paperless time.")
    ]
    closest_way: Annotated[str, Field(description="How to see what a rule really does.")]
    complete: bool
    not_checked: tuple[str, ...]
