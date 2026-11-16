"""The mail toolset: mail accounts, mail rules and processed mail (opt-in, off by default).

Paperless fetches mail from IMAP accounts and imports the attachments of the mails that a rule
matches as documents; after that the rule's action runs on the mail server (mark read, flag, tag,
move or delete the mail). Credentials never flow back (decision-6, decision-18): a password is
accepted when an account is created or changed and is never returned, quoted or logged, and the
same goes for OAuth tokens, which are set up in the Paperless web UI. Deleting an account or a
rule, fetching mail now, a rule that deletes or moves mail, pointing an account at another server
without a new password and handing a rule to another owner all need ``confirm`` (decision-15):
the refusal says what would happen.
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field, PlainValidator, SecretStr, WithJsonSchema

from paperless_mcp.domain.documents import NameOrId
from paperless_mcp.domain.mail import (
    AttachmentScope,
    CorrespondentSource,
    ImapSecurity,
    MailAccountDeletion,
    MailAccountInfo,
    MailAccountListing,
    MailAccountTested,
    MailFetchStarted,
    MailRuleDeletion,
    MailRuleInfo,
    MailRuleListing,
    MailRuleSettings,
    MailScope,
    NotText,
    PdfLayout,
    ProcessedMailListing,
    ProcessedStatus,
    RuleAction,
    RuleClearable,
    TitleSource,
)
from paperless_mcp.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "mail"
PAGE_SIZE = 25
LIST_LIMIT = 100
MAX_TEXT = 256

provider = LocalProvider()

type PositiveId = Annotated[int, Field(ge=1)]
type AccountId = Annotated[
    PositiveId, Field(description="Mail account id, from list_mail_accounts.")
]
type RuleId = Annotated[PositiveId, Field(description="Mail rule id, from list_mail_rules.")]
type Page = Annotated[int, Field(ge=1, description="Page number, from 1.")]
type Size = Annotated[int, Field(ge=1, le=LIST_LIMIT, description="Rows per page, at most 100.")]
type Text = Annotated[str, Field(max_length=MAX_TEXT)]


def secret(value: object) -> SecretStr:
    """A string as a SecretStr; anything else as ``NotText``, which the service refuses.

    It never raises: pydantic quotes a rejected input in its error (``input_value=``), FastMCP
    passes that to the client and records it in traces, and the input may be a password.
    """
    return SecretStr(value) if isinstance(value, str) else NotText()


WRITE_ONLY_HELP = (
    "The password of the mail account (or its app password), as text. Write-only: it is "
    "stored by Paperless and never shown again."
)

type Password = Annotated[
    SecretStr,
    PlainValidator(secret),
    WithJsonSchema(
        {"type": "string", "format": "password", "writeOnly": True, "description": WRITE_ONLY_HELP}
    ),
]
type Security = Annotated[
    ImapSecurity | None,
    Field(
        description="ssl: TLS from the start (port 993); starttls (port 143); none: no encryption."
    ),
]
type Filter = Annotated[
    Text | None,
    Field(
        description="Only mails matching this; wildcards where noted. Update: empty string clears."
    ),
]

# What a rule does to the mail it took documents from, spelled out for the model.
ACTIONS = (
    "mark_read (default; Paperless then skips read mail), flag (skips flagged mail), tag (adds the "
    "keyword in action_parameter), move (to the folder in action_parameter) or delete"
)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_mail_accounts"])
@boundary
async def list_mail_accounts(
    ctx: Context, page: Page = 1, page_size: Size = PAGE_SIZE
) -> MailAccountListing:
    """List the mail accounts Paperless fetches documents from.

    Each row has the server, port, security, username and account type, never a password or token.
    Needs the view permission for mail accounts. count is the total; pass next_page as page to
    see more. The rules of an account are in list_mail_rules.
    """
    return await services(ctx).mail.list_accounts(page=page, page_size=page_size)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_mail_account"])
@boundary
async def get_mail_account(ctx: Context, account_id: AccountId) -> MailAccountInfo:
    """Read one mail account: server, port, security, username, type. Never its password."""
    return await services(ctx).mail.get_account(account_id)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["create_mail_account"])
@boundary
async def create_mail_account(  # noqa: PLR0913 -- the settings of an account
    ctx: Context,
    *,
    name: Annotated[Text, Field(min_length=1, description="A unique name for the account.")],
    imap_server: Annotated[Text, Field(min_length=1, description="The IMAP host name.")],
    imap_port: Annotated[int, Field(ge=1, le=65535, description="The IMAP port, e.g. 993.")],
    username: Annotated[Text, Field(min_length=1, description="The login name.")],
    password: Password,
    imap_security: Security = None,
    character_set: Annotated[
        Text | None, Field(description="Character set of the server; default UTF-8.")
    ] = None,
) -> MailAccountInfo:
    """Add an IMAP account that Paperless can fetch mail from. The password is never returned.

    The password is stored by Paperless and cannot be read back, not by this server either; to
    change it, call update_mail_account with a new one. Security defaults to ssl. This only stores
    the account: use test_mail_account to try the login and create_mail_rule to choose which mails
    become documents. Gmail and Outlook OAuth accounts are set up in the Paperless web UI.
    """
    return await services(ctx).mail.create_account(
        name=name,
        imap_server=imap_server,
        imap_port=imap_port,
        username=username,
        password=password,
        imap_security=imap_security,
        character_set=character_set,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["update_mail_account"])
@boundary
async def update_mail_account(  # noqa: PLR0913 -- the settings of an account
    ctx: Context,
    account_id: AccountId,
    *,
    name: Annotated[Text | None, Field(min_length=1, description="New unique name.")] = None,
    imap_server: Annotated[Text | None, Field(min_length=1, description="New IMAP host.")] = None,
    imap_port: Annotated[int | None, Field(ge=1, le=65535, description="New IMAP port.")] = None,
    imap_security: Security = None,
    username: Annotated[Text | None, Field(min_length=1, description="New login name.")] = None,
    password: Annotated[
        Password | None, Field(description="A new password; omit to keep it.")
    ] = None,
    character_set: Annotated[Text | None, Field(description="New character set.")] = None,
    confirm: Confirm = False,
) -> MailAccountInfo:
    """Change a mail account; only what you pass changes, and the saved password stays.

    The password can be replaced but never read. Pointing the account at another server, port or
    security setting WITHOUT a new password would make Paperless send the saved password there,
    and turning encryption off sends it as plain text, so those need confirm: without it the call
    fails with an error that says what would happen, changes nothing and quotes no password;
    show that to the user and call again with confirm=true only once they agree.
    """
    return await services(ctx).mail.update_account(
        account_id,
        name=name,
        imap_server=imap_server,
        imap_port=imap_port,
        imap_security=imap_security,
        username=username,
        password=password,
        character_set=character_set,
        confirm=confirm,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_mail_account"])
@boundary
async def delete_mail_account(
    ctx: Context, account_id: AccountId, *, confirm: Confirm = False
) -> MailAccountDeletion:
    """Delete a mail account together with its rules and their processed-mail records.

    Paperless stops fetching from it; the mail on the server and the documents already imported
    stay. Without confirm it deletes nothing and fails with an error that names the account and
    its rules; describe that to the user and call again with confirm=true once they agree.
    """
    return await services(ctx).mail.delete_account(account_id, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["test_mail_account"])
@boundary
async def test_mail_account(ctx: Context, account_id: AccountId) -> MailAccountTested:
    """Try the login of a saved mail account: Paperless connects to its server and signs in.

    Changes nothing, on the server or in Paperless. A connection that fails is a normal result
    (success false) with a hint what to check, not an error. Uses the saved password, which this
    server never sees. Needs the change permission for the account. A server that accepts the
    connection but never answers is given up on after about 45 seconds.
    """
    return await services(ctx).mail.test_account(account_id)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["process_mail_account"])
@boundary
async def process_mail_account(
    ctx: Context, account_id: AccountId, *, confirm: Confirm = False
) -> MailFetchStarted:
    """Fetch the mail of an account now instead of at the next scheduled run (every 10 minutes).

    Paperless connects to the mail server in the background, imports documents from the mails
    that the account's enabled rules match, and then runs each rule's action on those mails ON
    THE MAIL SERVER: it can mark, flag or tag them, move them to another folder or DELETE them,
    and this server cannot undo that. Without confirm it fetches nothing and fails with an error
    that lists what each enabled rule would do; show that to the user and call again with
    confirm=true only once they agree. The result only says the fetch was queued: follow the
    outcome with list_processed_mail and the Paperless task list.
    """
    return await services(ctx).mail.process_account(account_id, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_mail_rules"])
@boundary
async def list_mail_rules(
    ctx: Context,
    account_id: Annotated[
        PositiveId | None, Field(description="Only the rules of this account; default: all.")
    ] = None,
    page: Page = 1,
    page_size: Size = PAGE_SIZE,
) -> MailRuleListing:
    """List the mail rules: which mails of an account become documents, and what happens next.

    Each row has the folder, filters, age limit, action (what runs on the mail server afterwards),
    and the tags, correspondent, document type and owner it assigns, with their names. Rules run
    in their order. count is the total; pass next_page as page to see more.
    """
    return await services(ctx).mail.list_rules(
        account_id=account_id, page=page, page_size=page_size
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_mail_rule"])
@boundary
async def get_mail_rule(ctx: Context, rule_id: RuleId) -> MailRuleInfo:
    """Read one mail rule with all its settings and the names of what it assigns."""
    return await services(ctx).mail.get_rule(rule_id)


def settings(**given: object) -> MailRuleSettings:
    """The rule settings the caller passed; what is left at None was not given."""
    return MailRuleSettings.model_validate({k: v for k, v in given.items() if v is not None})


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["create_mail_rule"])
@boundary
async def create_mail_rule(  # noqa: PLR0913 -- the settings of a rule
    ctx: Context,
    *,
    name: Annotated[Text, Field(min_length=1, description="The rule's name.")],
    account_id: AccountId,
    enabled: Annotated[bool | None, Field(description="Default true.")] = None,
    order: Annotated[int | None, Field(description="Rules run lowest first; default 0.")] = None,
    folder: Annotated[
        Text | None,
        Field(min_length=1, description="IMAP folder to read; default INBOX. Sub-folders: . or /."),
    ] = None,
    filter_from: Filter = None,
    filter_to: Filter = None,
    filter_subject: Filter = None,
    filter_body: Filter = None,
    filter_attachment_filename_include: Annotated[
        Text | None,
        Field(description="Only attachments whose whole name matches, e.g. *.pdf or *invoice*."),
    ] = None,
    filter_attachment_filename_exclude: Annotated[
        Text | None, Field(description="Skip attachments whose whole name matches.")
    ] = None,
    maximum_age_days: Annotated[
        int | None, Field(ge=0, le=36500, description="Only mails up to this old; default 30.")
    ] = None,
    attachment_type: Annotated[
        AttachmentScope | None,
        Field(description="attachments_only (default) or all_files (inline images too)."),
    ] = None,
    consumption_scope: Annotated[
        MailScope | None,
        Field(description="attachments (default), eml (the whole mail) or eml_and_attachments."),
    ] = None,
    pdf_layout: Annotated[
        PdfLayout | None,
        Field(description="How a mail becomes a PDF: default, text_html, html_text, html, text."),
    ] = None,
    action: Annotated[RuleAction | None, Field(description=f"After import: {ACTIONS}.")] = None,
    action_parameter: Annotated[
        Text | None, Field(description="The folder for move, the keyword for tag.")
    ] = None,
    assign_title_from: Annotated[
        TitleSource | None, Field(description="subject (default), filename or none.")
    ] = None,
    assign_tags: Annotated[
        list[NameOrId] | None,
        Field(max_length=50, description="Tag names or ids the documents get."),
    ] = None,
    assign_correspondent_from: Annotated[
        CorrespondentSource | None,
        Field(description="none (default), email_address, name or selected."),
    ] = None,
    assign_correspondent: Annotated[
        NameOrId | None,
        Field(description="Correspondent name or id; implies assign_correspondent_from selected."),
    ] = None,
    assign_document_type: Annotated[
        NameOrId | None, Field(description="Document type name or id.")
    ] = None,
    assign_owner_from_rule: Annotated[
        bool | None, Field(description="Documents get the rule's owner; default true.")
    ] = None,
    stop_processing: Annotated[
        bool | None, Field(description="No later rule runs once this one made a document.")
    ] = None,
    owner: Annotated[
        NameOrId | None, Field(description="Username or id of the rule's owner.")
    ] = None,
    confirm: Confirm = False,
) -> MailRuleInfo:
    """Create a rule that turns the attachments of matching mails into documents.

    Tags, correspondent, document type and owner are given by name or id. Unset options keep
    Paperless's defaults (INBOX, 30 days, attachments only, mark the mail read). A rule that is
    enabled and DELETES or MOVES mail on the mail server needs confirm: without it the call fails
    with an error that says which mails it would reach and what it does to them, creates nothing;
    show that to the user and call again with confirm=true only once they agree. Mark read,
    flag and tag need no confirm. An owner other than you needs confirm too. Needs the add
    permission for rules and the change permission
    for the account.
    """
    return await services(ctx).mail.create_rule(
        settings(
            name=name,
            account_id=account_id,
            enabled=enabled,
            order=order,
            folder=folder,
            filter_from=filter_from,
            filter_to=filter_to,
            filter_subject=filter_subject,
            filter_body=filter_body,
            filter_attachment_filename_include=filter_attachment_filename_include,
            filter_attachment_filename_exclude=filter_attachment_filename_exclude,
            maximum_age_days=maximum_age_days,
            attachment_type=attachment_type,
            consumption_scope=consumption_scope,
            pdf_layout=pdf_layout,
            action=action,
            action_parameter=action_parameter,
            assign_title_from=assign_title_from,
            assign_tags=None if assign_tags is None else tuple(assign_tags),
            assign_correspondent_from=assign_correspondent_from,
            assign_correspondent=assign_correspondent,
            assign_document_type=assign_document_type,
            assign_owner_from_rule=assign_owner_from_rule,
            stop_processing=stop_processing,
            owner=owner,
        ),
        confirm=confirm,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["update_mail_rule"])
@boundary
async def update_mail_rule(  # noqa: PLR0913 -- the settings of a rule
    ctx: Context,
    rule_id: RuleId,
    *,
    name: Annotated[Text | None, Field(min_length=1, description="New name.")] = None,
    account_id: Annotated[
        PositiveId | None, Field(description="Move the rule to this account.")
    ] = None,
    enabled: Annotated[bool | None, Field(description="Switch the rule on or off.")] = None,
    order: Annotated[int | None, Field(description="New position; lowest runs first.")] = None,
    folder: Annotated[Text | None, Field(min_length=1, description="New IMAP folder.")] = None,
    filter_from: Filter = None,
    filter_to: Filter = None,
    filter_subject: Filter = None,
    filter_body: Filter = None,
    filter_attachment_filename_include: Filter = None,
    filter_attachment_filename_exclude: Filter = None,
    maximum_age_days: Annotated[
        int | None, Field(ge=0, le=36500, description="New age limit in days.")
    ] = None,
    attachment_type: Annotated[AttachmentScope | None, Field(description="New scope.")] = None,
    consumption_scope: Annotated[MailScope | None, Field(description="New scope.")] = None,
    pdf_layout: Annotated[PdfLayout | None, Field(description="New PDF layout.")] = None,
    action: Annotated[RuleAction | None, Field(description=f"New action: {ACTIONS}.")] = None,
    action_parameter: Annotated[
        Text | None, Field(description="The folder for move, the keyword for tag; empty clears.")
    ] = None,
    assign_title_from: Annotated[TitleSource | None, Field(description="New title source.")] = None,
    assign_tags: Annotated[
        list[NameOrId] | None,
        Field(
            max_length=50, description="Replaces the tags the rule assigns; [] removes them all."
        ),
    ] = None,
    assign_correspondent_from: Annotated[
        CorrespondentSource | None, Field(description="New correspondent source.")
    ] = None,
    assign_correspondent: Annotated[
        NameOrId | None, Field(description="Correspondent name or id; implies selected.")
    ] = None,
    assign_document_type: Annotated[
        NameOrId | None, Field(description="Document type name or id.")
    ] = None,
    assign_owner_from_rule: Annotated[
        bool | None, Field(description="Documents get the rule's owner.")
    ] = None,
    stop_processing: Annotated[bool | None, Field(description="Stop later rules.")] = None,
    owner: Annotated[NameOrId | None, Field(description="Username or id of the new owner.")] = None,
    clear: Annotated[
        list[RuleClearable] | None,
        Field(
            max_length=3,
            description=(
                "Settings to empty: assign_correspondent, assign_document_type or owner (no "
                "owner makes the rule visible to every user with the mail rule permission)."
            ),
        ),
    ] = None,
    confirm: Confirm = False,
) -> MailRuleInfo:
    """Change a mail rule; only what you pass changes, and what is already so is not written.

    Names and ids work like in create_mail_rule; an empty string clears a filter or the action
    parameter. Needs confirm for (1) a rule that is enabled and deletes or moves mail when the
    change widens or redirects what it reaches (account, folder, filters, age, action, parameter,
    consumption scope, attachment type, switching it on), (2) changing the order or switching off
    stop_processing of an enabled rule while another enabled rule of the account deletes or moves
    mail, and (3) handing the rule to another owner or to nobody. Without it the call
    fails with an error that says what would happen and changes nothing; show that to the user
    and call again with confirm=true only once they agree.
    """
    return await services(ctx).mail.update_rule(
        rule_id,
        settings(
            name=name,
            account_id=account_id,
            enabled=enabled,
            order=order,
            folder=folder,
            filter_from=filter_from,
            filter_to=filter_to,
            filter_subject=filter_subject,
            filter_body=filter_body,
            filter_attachment_filename_include=filter_attachment_filename_include,
            filter_attachment_filename_exclude=filter_attachment_filename_exclude,
            maximum_age_days=maximum_age_days,
            attachment_type=attachment_type,
            consumption_scope=consumption_scope,
            pdf_layout=pdf_layout,
            action=action,
            action_parameter=action_parameter,
            assign_title_from=assign_title_from,
            assign_tags=None if assign_tags is None else tuple(assign_tags),
            assign_correspondent_from=assign_correspondent_from,
            assign_correspondent=assign_correspondent,
            assign_document_type=assign_document_type,
            assign_owner_from_rule=assign_owner_from_rule,
            stop_processing=stop_processing,
            owner=owner,
        ),
        clear=clear or (),
        confirm=confirm,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_mail_rule"])
@boundary
async def delete_mail_rule(
    ctx: Context, rule_id: RuleId, *, confirm: Confirm = False
) -> MailRuleDeletion:
    """Delete a mail rule and the records of the mail it processed.

    Paperless stops importing with it; the mail on the server and the documents already imported
    stay. Without confirm it deletes nothing and fails with an error that names the rule and its
    account; describe that to the user and call again with confirm=true once they agree.
    """
    return await services(ctx).mail.delete_rule(rule_id, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_processed_mail"])
@boundary
async def list_processed_mail(
    ctx: Context,
    rule_id: Annotated[
        PositiveId | None, Field(description="Only mail handled by this rule; default: all.")
    ] = None,
    status: Annotated[
        ProcessedStatus | None, Field(description="SUCCESS or FAILED; default: both.")
    ] = None,
    page: Page = 1,
    page_size: Size = PAGE_SIZE,
) -> ProcessedMailListing:
    """List the mails Paperless handled, newest first, as compact rows.

    Each row has the subject, folder, when it arrived and was processed, the rule, and the status
    (SUCCESS or FAILED with a short error). Use it to check a fetch or to find why a mail did
    not become a document. count is the total; pass next_page as page to see more.
    """
    return await services(ctx).mail.list_processed(
        rule_id=rule_id, status=status, page=page, page_size=page_size
    )
