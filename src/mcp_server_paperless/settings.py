"""Configuration read from environment variables.

The connection uses the ``PAPERLESS_*`` names other Paperless tools use as well; settings that
belong to this server use the ``PAPERLESS_MCP_*`` prefix.

``BaseSettings`` reads the environment in ``__init__``. Strict type checking sees only the
fields in the synthesized ``__init__``, so load with ``Model.model_validate({})``: pydantic calls
the custom ``__init__`` and the environment applies.
"""

import json
import os
import re
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, ClassVar, Literal, cast

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from mcp_server_paperless.domain.documents import MAX_UPLOAD_LIMIT
from mcp_server_paperless.domain.toolsets import ALL_TOOLSETS

# Directories that are views of the running system, not places for documents.
PSEUDO_FILE_SYSTEMS = ("/proc", "/sys", "/dev", "/run")

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _names(value: object, *, lower: bool) -> object:
    """A list setting: a JSON array or comma-separated names, trimmed, without duplicates."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    try:
        parsed = cast("list[object]", json.loads(text) if text.startswith("[") else text.split(","))
    except ValueError:
        message = (
            'not valid JSON; use a comma-separated list (core,pdf) or a JSON array (["core","pdf"])'
        )
        raise ValueError(message) from None
    names = [str(item).strip() for item in parsed]
    names = [name.lower() if lower else name for name in names if name]
    return tuple(dict.fromkeys(names))


class PaperlessSettings(BaseSettings):
    """Connection to the paperless-ngx instance.

    Attributes:
        url: Base URL this server uses to reach Paperless (``PAPERLESS_URL``).
        token: API token (``PAPERLESS_TOKEN``); masked in ``repr`` and ``str``.
        public_url: Base URL for links handed to users when it differs from ``url``
            (``PAPERLESS_PUBLIC_URL``). Empty values count as unset.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PAPERLESS_", frozen=True, env_ignore_empty=True
    )

    url: HttpUrl
    token: SecretStr = Field(min_length=1)
    public_url: HttpUrl | None = None


class ServerSettings(BaseSettings):
    """Settings of this MCP server.

    Attributes:
        log_level: Minimum level of log records written to stderr
            (``PAPERLESS_MCP_LOG_LEVEL``).
        startup_timeout: Most seconds the startup check of the Paperless connection may take
            (``PAPERLESS_MCP_STARTUP_TIMEOUT``); a Paperless that does not answer in time does
            not stop the start.
        toolsets: Toolsets whose tools are offered (``PAPERLESS_MCP_TOOLSETS``): names, or
            ``all``; comma-separated or a JSON array. Default ``core,ai``; ``ai`` is hidden
            while Paperless AI is disabled. The ai tools are read-only but each call may cost
            money (Paperless asks its LLM backend), so clients that auto-approve read-only tools
            will run them unasked: set ``core`` to leave them out, or name them in
            ``PAPERLESS_MCP_DISABLED_TOOLS`` (``ask_paperless_ai,get_ai_suggestions``).
            ``raw`` (``paperless_api_get``, read-only GET under ``/api/``) is off by default;
            ``all`` selects it (decision-25).
        read_only: Offer only tools that do not change anything (``PAPERLESS_MCP_READ_ONLY``).
        disabled_tools: Tools never offered, whatever the toolsets say
            (``PAPERLESS_MCP_DISABLED_TOOLS``).
        max_download_bytes: Largest file ``download_document`` attaches
            (``PAPERLESS_MCP_MAX_DOWNLOAD_BYTES``, 1 byte to 32 MiB); larger files are reported
            with their link only. An embedded file travels base64-encoded (a third larger) in one
            MCP message, and the FastMCP client's default limit for one HTTP event is 1 MiB, so
            the default of 512 KiB (about 700 KB on the wire) fits that with room for the report.
        max_upload_bytes: Largest file ``upload_document`` takes, base64 content or a file read
            by path (``PAPERLESS_MCP_MAX_UPLOAD_BYTES``, 1 byte to 100 MiB). Base64 text is a
            third larger than the file and arrives in one tool call that the caller's model has
            to write out, so real uploads are far smaller; the default of 10 MiB covers a scan
            of many pages. Decoding is linear (about a gigabyte a second), but the text, the
            decoded bytes and the request body are all in memory at once, for one upload over HTTP
            about 6 to 9 times the file (measured): the limit is checked on the text before
            anything is decoded.
        upload_dirs: Directories ``upload_document`` may read files from, by path
            (``PAPERLESS_MCP_UPLOAD_DIRS``): absolute paths, comma-separated or a JSON array.
            Empty (the default) switches uploading by path off. Only the stdio transport ever
            offers a path parameter, and only when this is set (decision-7). The file system
            root, the home directory itself, /proc, /sys, /dev and /run are refused as
            entries, and files and folders whose name starts with a dot below an entry are never
            read (``.ssh``, ``.env``).
        download_dirs: Directories ``download_document`` and ``bulk_download`` may save files into
            (``PAPERLESS_MCP_DOWNLOAD_DIRS``), with the rules of ``upload_dirs``. Empty (the
            default): nothing is ever saved. Only the stdio transport offers it, and only without
            ``read_only`` (decision-45).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PAPERLESS_MCP_", frozen=True
    )

    log_level: LogLevel = "INFO"
    startup_timeout: float = Field(default=5.0, gt=0)
    toolsets: Annotated[tuple[str, ...], NoDecode] = ("core", "ai")
    read_only: bool = False
    disabled_tools: Annotated[tuple[str, ...], NoDecode] = ()
    max_download_bytes: int = Field(default=512 * 1024, ge=1, le=32 * 1024 * 1024)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=MAX_UPLOAD_LIMIT)
    upload_dirs: Annotated[tuple[str, ...], NoDecode] = ()
    download_dirs: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("toolsets", mode="before")
    @classmethod
    def _split_toolsets(cls, value: object) -> object:
        return _names(value, lower=True)

    @field_validator("disabled_tools", mode="before")
    @classmethod
    def _split_disabled_tools(cls, value: object) -> object:
        return _names(value, lower=False)

    @field_validator("upload_dirs", "download_dirs", mode="before")
    @classmethod
    def _split_directories(cls, value: object) -> object:
        return _names(value, lower=False)

    @field_validator("upload_dirs", "download_dirs", mode="after")
    @classmethod
    def _check_directories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for directory in value:
            if not Path(directory).is_absolute():
                message = f"{directory!r} is not an absolute path; name each directory in full"
                raise ValueError(message)
            real = os.path.realpath(directory)
            if real == os.path.realpath(os.sep):
                message = "the file system root is too much to allow; name the directories"
                raise ValueError(message)
            if real == os.path.realpath(Path("~").expanduser()):
                message = "the home directory itself is too much to allow; name a folder in it"
                raise ValueError(message)
            if any(real == fs or real.startswith(fs + os.sep) for fs in PSEUDO_FILE_SYSTEMS):
                message = "/proc, /sys, /dev and /run hold no documents; name a folder with files"
                raise ValueError(message)
        return value

    @field_validator("toolsets", mode="after")
    @classmethod
    def _check_toolsets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            message = "name at least one toolset, or all"
            raise ValueError(message)
        if ALL_TOOLSETS in value and len(value) > 1:
            message = f"{ALL_TOOLSETS} stands alone; do not combine it with toolset names"
            raise ValueError(message)
        return value


# Tokens travel in an Authorization header: RFC 6750 token68 characters only.
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]+")
_HOSTNAME = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")
MIN_TOKEN_LENGTH = 16


def _is_host(entry: str) -> bool:
    """A host name, an IPv4 address or an IPv6 address (optionally in brackets); no port."""
    try:
        _ = ip_address(entry.removeprefix("[").removesuffix("]"))
    except ValueError:
        return _HOSTNAME.fullmatch(entry) is not None
    return True


class HttpSettings(BaseSettings):
    """Settings of the HTTP transport (``mcp-server-paperless-http``).

    Attributes:
        host: Address to bind (``PAPERLESS_MCP_HTTP_HOST``). Anything but a loopback address
            counts as remote and needs ``auth_tokens`` and ``allowed_hosts``.
        port: Port to bind (``PAPERLESS_MCP_HTTP_PORT``).
        path: Path of the MCP endpoint (``PAPERLESS_MCP_HTTP_PATH``).
        auth_tokens: Static bearer tokens that may use the endpoint
            (``PAPERLESS_MCP_HTTP_AUTH_TOKENS``): comma-separated or a JSON array, each at least
            16 characters of ``A-Za-z0-9._~+/=-``; masked in ``repr`` and ``str``.
        allowed_hosts: Host names clients use to reach the server
            (``PAPERLESS_MCP_HTTP_ALLOWED_HOSTS``), plain names without wildcards or ports;
            the loopback names are always allowed. Required unless bound to loopback.
        allow_unauthenticated_loopback: Serve without tokens, only while bound to loopback
            (``PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK``).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PAPERLESS_MCP_HTTP_", frozen=True
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    path: str = "/mcp"
    auth_tokens: Annotated[tuple[SecretStr, ...], NoDecode] = ()
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    allow_unauthenticated_loopback: bool = False

    @field_validator("auth_tokens", mode="before")
    @classmethod
    def _split_tokens(cls, value: object) -> object:
        return _names(value, lower=False)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        return _names(value, lower=True)

    @field_validator("auth_tokens", mode="after")
    @classmethod
    def _check_tokens(cls, value: tuple[SecretStr, ...]) -> tuple[SecretStr, ...]:
        for token in value:
            secret = token.get_secret_value()
            # The message never carries the token.
            if len(secret) < MIN_TOKEN_LENGTH:
                message = (
                    f"a token needs at least {MIN_TOKEN_LENGTH} characters (openssl rand -hex 32)"
                )
                raise ValueError(message)
            if _TOKEN.fullmatch(secret) is None:
                message = "a token may contain only letters, digits and . _ ~ + / = -"
                raise ValueError(message)
        return value

    @field_validator("allowed_hosts", mode="after")
    @classmethod
    def _check_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for entry in value:
            if not _is_host(entry):
                message = (
                    f"{entry!r} is not a plain host name or address (no wildcard, path or port)"
                )
                raise ValueError(message)
        return value

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        segments = value.split("/")[1:]
        if re.fullmatch(r"/[A-Za-z0-9._~/-]+", value) is None or any(
            segment in {"", ".", ".."} for segment in segments
        ):
            message = "use an absolute path below the root such as /mcp"
            raise ValueError(message)
        return value


# The callback of claude.ai's custom connectors (docs/vendor/fastmcp oauth-proxy.mdx).
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
# Anthropic has said the callback may move to claude.com; both domains are Anthropic's.
CLAUDE_COM_CALLBACK = "https://claude.com/api/mcp/auth_callback"
# Clients on this machine (the FastMCP client, Claude Code) call back on a loopback port.
LOOPBACK_CALLBACKS = ("http://localhost:*", "http://127.0.0.1:*")
_LOOPBACK_NAMES = frozenset({"localhost", "[::1]"})
# A callback clients may register: https on a named host, or plain http on loopback; a * only
# for the port or in the path, never in the host.
_REDIRECTS = (
    re.compile(r"https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:(\d+|\*))?(/\S*)?", re.IGNORECASE),
    re.compile(r"http://(localhost|127\.0\.0\.1|\[::1\])(:(\d+|\*))?(/\S*)?", re.IGNORECASE),
)
MIN_SIGNING_KEY_LENGTH = 32


def _loopback_url(url: HttpUrl) -> bool:
    host = (url.host or "").lower()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _https_or_loopback(url: HttpUrl) -> HttpUrl:
    if url.scheme != "https" and not _loopback_url(url):
        message = "use https (plain http only for a loopback address)"
        raise ValueError(message)
    return url


class OidcSettings(BaseSettings):
    """The optional OIDC layer of the HTTP transport (decision-31).

    Off while none of its variables is set; then the HTTP transport authenticates with the static
    bearer tokens alone. Once any is set, the five required ones must all be set, or the server
    refuses to start (``app.check_startup`` names the missing ones). Empty values count as unset.

    Attributes:
        config_url: The identity provider's discovery document, ``.../.well-known/
            openid-configuration`` (``PAPERLESS_MCP_OIDC_CONFIG_URL``); https unless loopback.
        client_id: The client id of this server at the identity provider
            (``PAPERLESS_MCP_OIDC_CLIENT_ID``).
        client_secret: Its client secret (``PAPERLESS_MCP_OIDC_CLIENT_SECRET``); masked.
        base_url: The public origin of this server, as clients and browsers reach it
            (``PAPERLESS_MCP_OIDC_BASE_URL``, e.g. ``https://mcp.example.org``): https unless
            loopback, no path. The identity provider must allow ``<base_url>/auth/callback``.
        jwt_signing_key: Secret that signs the tokens this server issues and encrypts its OAuth
            storage (``PAPERLESS_MCP_OIDC_JWT_SIGNING_KEY``, at least 32 characters,
            ``openssl rand -hex 32``); masked. Changing it signs every client out.
        storage_dir: Directory for client registrations and sessions, encrypted
            (``PAPERLESS_MCP_OIDC_STORAGE_DIR``, absolute, writable). Unset: in memory, so a
            restart signs every OIDC client out.
        allowed_redirect_uris: Callbacks OAuth clients may register
            (``PAPERLESS_MCP_OIDC_ALLOWED_REDIRECT_URIS``): https URLs or loopback http, a
            ``*`` for a port or in the path; default the claude.ai and claude.com callbacks and
            loopback ports.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PAPERLESS_MCP_OIDC_", frozen=True, env_ignore_empty=True
    )

    REQUIRED: ClassVar[tuple[str, ...]] = (
        "config_url",
        "client_id",
        "client_secret",
        "base_url",
        "jwt_signing_key",
    )

    config_url: HttpUrl | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    base_url: HttpUrl | None = None
    jwt_signing_key: SecretStr | None = None
    storage_dir: str | None = None
    allowed_redirect_uris: Annotated[tuple[str, ...], NoDecode] = (
        CLAUDE_CALLBACK,
        CLAUDE_COM_CALLBACK,
        *LOOPBACK_CALLBACKS,
    )

    @property
    def configured(self) -> bool:
        """Whether any OIDC variable is set: then the layer is on, or the start is refused."""
        return bool(self.model_fields_set)

    def missing(self) -> list[str]:
        """The required variables that are not set, by name, while the layer is configured."""
        if not self.configured:
            return []
        prefix = self.model_config.get("env_prefix") or ""
        return sorted(
            f"{prefix}{name.upper()}" for name in self.REQUIRED if getattr(self, name) is None
        )

    @field_validator("allowed_redirect_uris", mode="before")
    @classmethod
    def _split_redirects(cls, value: object) -> object:
        return _names(value, lower=False)

    @field_validator("allowed_redirect_uris", mode="after")
    @classmethod
    def _check_redirects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            message = f"name at least one callback, such as {CLAUDE_CALLBACK}"
            raise ValueError(message)
        for entry in value:
            if not any(pattern.fullmatch(entry) for pattern in _REDIRECTS):
                message = (
                    f"{entry!r} is no https URL or loopback http URL (a * only for a port or "
                    "in the path)"
                )
                raise ValueError(message)
        return value

    @field_validator("config_url")
    @classmethod
    def _check_config_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return None
        # Refused, so that no message (the discovery refusal names the URL) ever carries them.
        if value.username is not None or value.password is not None:
            message = "the discovery URL may not carry credentials"
            raise ValueError(message)
        return _https_or_loopback(value)

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return None
        if (
            value.path not in {None, "", "/"}
            or value.query is not None
            or value.fragment is not None
            or value.username is not None
            or value.password is not None
        ):
            message = "use the origin only, such as https://mcp.example.org (no path or query)"
            raise ValueError(message)
        return _https_or_loopback(value)

    @field_validator("client_id")
    @classmethod
    def _check_client_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            message = "the client id may not be blank"
            raise ValueError(message)
        return value

    @field_validator("jwt_signing_key")
    @classmethod
    def _check_signing_key(cls, value: SecretStr | None) -> SecretStr | None:
        # The message never carries the key.
        if value is not None and len(value.get_secret_value()) < MIN_SIGNING_KEY_LENGTH:
            message = (
                f"the signing key needs at least {MIN_SIGNING_KEY_LENGTH} characters "
                "(openssl rand -hex 32)"
            )
            raise ValueError(message)
        return value

    @field_validator("storage_dir")
    @classmethod
    def _check_storage_dir(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).is_absolute():
            message = f"{value!r} is not an absolute path"
            raise ValueError(message)
        return value
