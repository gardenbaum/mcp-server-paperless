"""The admin toolset: users, groups, application configuration and logs.

The toolset's ``bulk_download`` is in ``downloads``, beside the download of one file.

Opt-in (``PAPERLESS_MCP_TOOLSETS=core,admin``) and mostly for superusers. Listing users and groups
stays with ``list_users`` and ``list_groups`` of the permissions toolset; these tools take a
username or group name, or an id, so they work without it. Anything that gives or takes access, an
account deletion, every change of the configuration and the tasks that retrain or re-index need
``confirm`` (decision-26, decision-15). Passwords and API keys are write-only (decision-18,
decision-26).
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field, JsonValue, PlainValidator, SecretStr, WithJsonSchema

from mcp_server_paperless.domain.admin import (
    CONFIG_SETTINGS,
    AppConfig,
    ConfigOutcome,
    Deletion,
    GroupDetail,
    GroupOutcome,
    LogExcerpt,
    LogList,
    NotText,
    UserDetail,
    UserOutcome,
)
from mcp_server_paperless.domain.documents import NameOrId
from mcp_server_paperless.services.admin_system import (
    DEFAULT_LOG_LINES,
    MAX_LOG_LINES,
)
from mcp_server_paperless.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "admin"
PAGE_SIZE = 25
LIST_LIMIT = 100

provider = LocalProvider()

type PositiveId = Annotated[int, Field(ge=1)]
type UserRef = Annotated[
    NameOrId, Field(description="Username or id; the ids are in list_users (permissions toolset).")
]
type GroupRef = Annotated[
    NameOrId,
    Field(description="Group name or id; the ids are in list_groups (permissions toolset)."),
]
type Groups = Annotated[
    list[NameOrId] | None,
    Field(description="Names or ids of the groups the user is in; replaces the user's groups."),
]
type Permissions = Annotated[
    list[str] | None,
    Field(
        description=(
            "Permission codenames without the app label, such as view_document or change_tag; "
            "replaces the list the user has. Codenames are shown by get_user and get_group."
        )
    ),
]
type Page = Annotated[int, Field(ge=1, description="Page number, from 1.")]
type Size = Annotated[int, Field(ge=1, le=LIST_LIMIT, description="Rows per page, at most 100.")]


def secret(value: object) -> SecretStr:
    """A string as a SecretStr; anything else as ``NotText``, which the service refuses.

    It never raises: pydantic quotes a rejected input in its error (``input_value=``), FastMCP
    passes that to the client and records it in traces, and the input may be a secret.
    """
    return SecretStr(value) if isinstance(value, str) else NotText()


def write_only(what: str) -> WithJsonSchema:
    """The schema of a write-only string: a password-format, ``writeOnly`` text."""
    return WithJsonSchema(
        {
            "type": "string",
            "format": "password",
            "writeOnly": True,
            "description": f"{what} Write-only: Paperless stores it and it is never shown again.",
        }
    )


type Password = Annotated[
    SecretStr, PlainValidator(secret), write_only("The password of the user, as text.")
]
type LlmKey = Annotated[
    SecretStr, PlainValidator(secret), write_only("The API key of the LLM service.")
]
type EmbeddingKey = Annotated[
    SecretStr, PlainValidator(secret), write_only("The API key of the embedding service.")
]
type OcrKey = Annotated[
    SecretStr, PlainValidator(secret), write_only("The API key of the remote OCR service.")
]

SETTINGS_HELP = "\n".join(f"- {name}: {text}" for name, text in CONFIG_SETTINGS.items())


# --- users


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_user"])
@boundary
async def get_user(ctx: Context, user: UserRef) -> UserDetail:
    """Read one user: names, e-mail, active, staff and superuser flags, groups and permissions.

    Never returns a password or token. Superusers see and change everything, whatever is granted;
    staff users may sign in to the Django admin site and read the logs. Needs the view_user
    permission in Paperless. Find users with
    list_users (permissions toolset).
    """
    return await services(ctx).accounts.get_user(user)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["create_user"])
@boundary
async def create_user(  # noqa: PLR0913 -- one parameter per field, described for the LLM
    ctx: Context,
    username: Annotated[
        str,
        Field(
            min_length=1,
            max_length=150,
            description="Letters, digits and @ . + - _ only, at most 150 characters; unique.",
        ),
    ],
    *,
    password: Annotated[
        Password | None, Field(description="Initial password; omit for none.")
    ] = None,
    email: Annotated[str | None, Field(description="E-mail address.")] = None,
    first_name: Annotated[str | None, Field(max_length=150, description="First name.")] = None,
    last_name: Annotated[str | None, Field(max_length=150, description="Last name.")] = None,
    is_active: Annotated[bool | None, Field(description="May sign in (default true).")] = None,
    is_staff: Annotated[
        bool | None,
        Field(description="Staff status: may sign in to the Django admin site and read the logs."),
    ] = None,
    is_superuser: Annotated[
        bool | None, Field(description="Superuser: sees and changes everything.")
    ] = None,
    groups: Groups = None,
    permissions: Permissions = None,
    confirm: Confirm = False,
) -> UserOutcome:
    """Create a Paperless user. The password is write-only and never shown again.

    Creating a superuser or a staff user, or a user with a password, groups or permissions, needs
    confirm: without it a dialog, or an error for you to show the user, first says what the account
    would be allowed to do (see confirm). A user with only names and an e-mail address needs none.
    Groups are names or ids; unknown ones are an error before anything is created. Only a superuser
    may make superusers or staff users; otherwise the add_user permission is enough.
    """
    return await services(ctx).accounts.create_user(
        username=username,
        password=password,
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
        groups=groups,
        permissions=permissions,
        confirm=confirm,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["update_user"])
@boundary
async def update_user(  # noqa: PLR0913 -- one parameter per field, described for the LLM
    ctx: Context,
    user: UserRef,
    *,
    username: Annotated[
        str | None, Field(min_length=1, max_length=150, description="New username.")
    ] = None,
    password: Annotated[
        Password | None, Field(description="A new password; omit to keep it.")
    ] = None,
    email: Annotated[
        str | None, Field(description="New address; an empty string removes it.")
    ] = None,
    first_name: Annotated[str | None, Field(max_length=150, description="New first name.")] = None,
    last_name: Annotated[str | None, Field(max_length=150, description="New last name.")] = None,
    is_active: Annotated[
        bool | None, Field(description="false: the user can no longer sign in.")
    ] = None,
    is_staff: Annotated[
        bool | None,
        Field(description="Staff status: may sign in to the Django admin site and read the logs."),
    ] = None,
    is_superuser: Annotated[
        bool | None, Field(description="Superuser: sees and changes everything.")
    ] = None,
    groups: Groups = None,
    permissions: Permissions = None,
    confirm: Confirm = False,
) -> UserOutcome:
    """Change a user; only what you pass changes and the saved password stays.

    Anything that gives or takes away access or can take the account over needs confirm: a new
    e-mail address (it resets the password), a rename, superuser or staff status, deactivating or
    reactivating, a new password, groups, permissions. Without it a dialog, or an error for you to
    show the user, first names who is affected (see confirm). First and last name need none, nor
    does clearing the e-mail address. Lists replace the user's lists. The account this server signs
    in with cannot be deactivated or stripped of superuser status. A call that changes nothing says
    so. Only a superuser may change a superuser or the superuser and staff status of anyone;
    otherwise the change_user permission is enough.
    """
    return await services(ctx).accounts.update_user(
        user,
        username=username,
        password=password,
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
        groups=groups,
        permissions=permissions,
        confirm=confirm,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_user"])
@boundary
async def delete_user(ctx: Context, user: UserRef, *, confirm: Confirm = False) -> Deletion:
    """Delete a user for good. Objects they own lose their owner, their grants go.

    Deleting cannot be undone, so it needs confirm: without it a dialog, or an error for you to
    show the user, first says what follows (see confirm). Deactivating with
    update_user(is_active=false) keeps the account. The account this server signs in with cannot be
    deleted. Needs the delete_user permission.
    """
    return await services(ctx).accounts.delete_user(user, confirm=confirm)


# --- groups


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_group"])
@boundary
async def get_group(ctx: Context, group: GroupRef) -> GroupDetail:
    """Read one group: its name and the permission codenames it grants to its members.

    Needs the view_group permission in Paperless. Find groups with list_groups (permissions
    toolset).
    """
    return await services(ctx).accounts.get_group(group)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["create_group"])
@boundary
async def create_group(
    ctx: Context,
    name: Annotated[str, Field(min_length=1, max_length=150, description="Unique group name.")],
    permissions: Annotated[
        list[str] | None,
        Field(description="Permission codenames the group grants, such as view_document."),
    ] = None,
    *,
    confirm: Confirm = False,
) -> GroupOutcome:
    """Create a group, optionally with the permissions it grants to its members.

    A group without permissions needs no confirm. One that grants permissions does, because
    putting users into it gives them those rights: without it a dialog, or an error for you to show
    the user, first names the permissions (see confirm). Add users with update_user(groups=). Needs
    the add_group permission.
    """
    return await services(ctx).accounts.create_group(name, permissions, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["update_group"])
@boundary
async def update_group(
    ctx: Context,
    group: GroupRef,
    *,
    name: Annotated[
        str | None, Field(min_length=1, max_length=150, description="New name.")
    ] = None,
    permissions: Annotated[
        list[str] | None,
        Field(description="Permission codenames; replaces everything the group grants."),
    ] = None,
    confirm: Confirm = False,
) -> GroupOutcome:
    """Rename a group or replace the permissions it grants to its members.

    Changing the permissions needs confirm: without it a dialog, or an error for you to show the
    user, first names what the members gain and lose (see confirm). A rename needs none. A call
    that changes nothing says so. Needs the change_group permission.
    """
    return await services(ctx).accounts.update_group(
        group, name=name, permissions=permissions, confirm=confirm
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_group"])
@boundary
async def delete_group(ctx: Context, group: GroupRef, *, confirm: Confirm = False) -> Deletion:
    """Delete a group for good; its members lose what it granted them.

    Needs confirm: without it a dialog, or an error for you to show the user, first says what
    follows (see confirm). Needs the delete_group permission.
    """
    return await services(ctx).accounts.delete_group(group, confirm=confirm)


# --- configuration


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_app_config"])
@boundary
async def get_app_config(ctx: Context) -> AppConfig:
    """Read the application configuration: the settings that have a value, OCR, barcodes, AI.

    Settings without a value follow the server's environment and are left out. API keys are
    never returned; keys_configured names those that hold one. URLs come without their
    credentials; shown_in_part names those that may have some, and writing one back as shown
    keeps them. Any signed-in user may read it. Change it with update_app_config.
    """
    return await services(ctx).system.get_config()


UPDATE_CONFIG_DESCRIPTION = f"""Change the application configuration: OCR, barcodes, title, AI.

It applies to every user and to every document consumed from now on, and the AI settings decide
which service gets the document text, so every change needs confirm: without it a dialog, or an
error for you to show the user, first lists each setting with its old and new value, never a key
(see confirm). Settings you leave out stay. A service URL written back as it was returned keeps
its stored credentials; with an empty user name (https://@host/path) it loses them. A call that
changes nothing says so. Needs the change_applicationconfiguration permission. Settings:
{SETTINGS_HELP}
"""


@provider.tool(
    tags={TOOLSET},
    annotations=ANNOTATIONS["update_app_config"],
    description=UPDATE_CONFIG_DESCRIPTION,
)
@boundary
async def update_app_config(  # noqa: PLR0913 -- the settings and one write-only key each
    ctx: Context,
    settings: Annotated[
        dict[str, JsonValue] | None,
        Field(
            description=(
                "Settings to change, by name; null resets one to the server's environment. "
                "Names Paperless does not know are refused."
            )
        ),
    ] = None,
    *,
    llm_api_key: Annotated[LlmKey | None, Field(description="A new LLM API key.")] = None,
    llm_embedding_api_key: Annotated[
        EmbeddingKey | None, Field(description="A new embedding API key.")
    ] = None,
    remote_ocr_api_key: Annotated[
        OcrKey | None, Field(description="A new remote OCR API key.")
    ] = None,
    confirm: Confirm = False,
) -> ConfigOutcome:
    """Change the application configuration (the description lists the settings)."""
    keys = {
        name: key
        for name, key in (
            ("llm_api_key", llm_api_key),
            ("llm_embedding_api_key", llm_embedding_api_key),
            ("remote_ocr_api_key", remote_ocr_api_key),
        )
        if key is not None
    }
    return await services(ctx).system.update_config(settings, keys=keys, confirm=confirm)


# --- logs


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_logs"])
@boundary
async def list_logs(ctx: Context) -> LogList:
    """List the log files this instance keeps (paperless, mail, celery). Staff users only."""
    return await services(ctx).system.list_logs()


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_log"])
@boundary
async def get_log(
    ctx: Context,
    name: Annotated[str, Field(description="Log name, from list_logs.")],
    lines: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_LOG_LINES,
            description=f"How many of the newest lines, at most {MAX_LOG_LINES}.",
        ),
    ] = DEFAULT_LOG_LINES,
) -> LogExcerpt:
    """Read the newest lines of a log, oldest first, to find out why something failed.

    The text is cut to about 20 000 characters, dropping the oldest lines (truncated says so).
    Lines can name documents and mail subjects; URLs in them come without user name and password
    (note says so). Staff users only.
    """
    return await services(ctx).system.get_log(name, lines=lines)
