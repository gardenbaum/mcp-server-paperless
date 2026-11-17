"""Administration: users, groups, the application configuration, logs, tasks, bulk download.

Credentials never flow back (decision-18, decision-26): a user here has no password field and the
configuration has no API key, only the names of the keys that hold a value. A password or a key
only goes in, as a ``SecretStr`` whose representation hides it.

The ports read and write plain records; the tools speak names, and their results carry both
(see ``UserDetail`` and ``GroupDetail``). Output models are tool output schemas: a one-line class
docstring each, and field descriptions only where the name does not say enough.
"""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from paperless_mcp.domain.errors import Validation
from paperless_mcp.domain.permissions import Principal
from paperless_mcp.domain.tasks import TaskState

type TaskType = Literal[
    "consume_file",
    "train_classifier",
    "sanity_check",
    "index_optimize",
    "mail_fetch",
    "llm_index",
    "empty_trash",
    "check_workflows",
    "bulk_update",
    "reprocess_document",
    "build_share_link",
    "bulk_delete",
    "apply_ai_suggestions",
]
# The tasks Paperless 3.4 lets a superuser start by hand (TasksViewSet._RUNNABLE_TASKS).
type RunnableTask = Literal["train_classifier", "sanity_check", "llm_index"]
type ArchiveContent = Literal["archive", "originals", "both"]
type Compression = Literal["none", "deflated", "bzip2", "lzma"]

# The settings of the application configuration a caller may change: name, then what it does.
# Paperless ignores names it does not know without an error, so the service refuses them. The
# logo is a file upload and stays in the web UI; the API keys are write-only (CONFIG_SECRETS).
CONFIG_SETTINGS: Final[dict[str, str]] = {
    "output_type": "Output PDF type: pdf, pdfa, pdfa-1, pdfa-2 or pdfa-3.",
    "pages": "OCR only pages 1 to this number (at least 1).",
    "language": "OCR languages, e.g. 'eng+deu'.",
    "mode": "OCR mode: auto, force, redo or off.",
    "archive_file_generation": "Archive PDF generation: auto, always or never.",
    "image_dpi": "Fallback DPI for images without one (at least 1).",
    "unpaper_clean": "Unpaper cleaning: clean, clean-final or none.",
    "deskew": "Straighten skewed pages (true or false).",
    "rotate_pages": "Rotate pages automatically (true or false).",
    "rotate_pages_threshold": "Confidence needed to rotate a page (number, at least 0).",
    "max_image_pixels": "Largest image to decompress (number, at least 0).",
    "color_conversion_strategy": (
        "Ghostscript colour conversion: LeaveColorUnchanged, RGB, UseDeviceIndependentColor, "
        "Gray or CMYK."
    ),
    "app_title": "Application title shown in the web UI (at most 48 characters).",
    "barcodes_enabled": "Scan barcodes (true or false).",
    "barcode_enable_tiff_support": "Scan barcodes in TIFF files (true or false).",
    "barcode_string": "Barcode text that splits documents (at most 32 characters).",
    "barcode_retain_split_pages": "Keep the pages that carry a split barcode (true or false).",
    "barcode_enable_asn": "Read archive serial numbers from barcodes (true or false).",
    "barcode_asn_prefix": "Prefix of ASN barcodes (at most 32 characters).",
    "barcode_upscale": "Barcode upscale factor (number, at least 1).",
    "barcode_dpi": "DPI for barcode scanning (at least 1).",
    "barcode_max_pages": "Most pages scanned for barcodes (at least 1).",
    "barcode_enable_tag": "Assign tags from barcodes (true or false).",
    "barcode_tag_split": "Split on tag barcodes (true or false).",
    "barcode_store_values": "Store the values of detected barcodes (true or false).",
    "barcode_tag_mapping": "Barcode to tag mapping: an object of regular expression to tag.",
    "user_args": "Extra OCRmyPDF arguments: a JSON object.",
    "remote_ocr_engine": "Remote OCR engine: azureai.",
    "remote_ocr_endpoint": "Remote OCR endpoint URL.",
    "remote_ocr_mode": "Which documents go to remote OCR: always or workflow_only.",
    "ai_enabled": "Switch the AI features on (true or false).",
    "llm_embedding_backend": "Embedding backend: openai-like, huggingface or ollama.",
    "llm_embedding_model": "Embedding model name.",
    "llm_embedding_endpoint": "Embedding endpoint URL.",
    "llm_embedding_chunk_size": "Embedding chunk size (at least 1).",
    "llm_context_size": "LLM context size (at least 1).",
    "llm_backend": "LLM backend: openai-like or ollama.",
    "llm_model": "LLM model name.",
    "llm_endpoint": "LLM endpoint URL.",
    "llm_output_language": "Language of LLM answers.",
    "llm_request_timeout": "LLM timeout in seconds (at least 1).",
}
# Settings that are keys: write-only, and only whether one is saved is ever read (decision-26).
CONFIG_SECRETS: Final = ("llm_api_key", "llm_embedding_api_key", "remote_ocr_api_key")
# Settings Paperless sends that cannot be changed through these tools (read-only).
CONFIG_READ_ONLY: Final = ("app_logo",)
# Settings whose change decides where document text is sent to: confirmations name them.
CONFIG_OUTBOUND: Final = frozenset(
    {"ai_enabled", "llm_backend", "llm_endpoint", "llm_embedding_backend", "llm_embedding_endpoint"}
    | {"remote_ocr_engine", "remote_ocr_endpoint", "remote_ocr_mode"}
)


def masked_secret(what: str) -> Validation:
    """The error for a secret made of asterisks only, which Paperless would not store."""
    message = (
        f"The {what} cannot consist only of asterisks: Paperless takes that to mean 'keep the "
        "saved one'."
    )
    return Validation(message, hint=f"Leave the {what} out to keep the saved one.")


def check_secret(secret: str | None, what: str) -> None:
    """Refuse a secret that Paperless would ignore as 'unchanged'.

    Raises:
        Validation: ``secret`` is not empty and made of asterisks only.
    """
    if secret is not None and secret and not secret.strip("*"):
        raise masked_secret(what)


class NotText(SecretStr):
    """Stands in for a secret that was not text; the service refuses it without quoting it."""

    def __init__(self) -> None:
        """Hold nothing."""
        super().__init__("")


class UserRecord(BaseModel):
    """A user as the port reads it. It has no password: nothing here can leak one."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    username: str
    email: str | None
    first_name: str
    last_name: str
    is_active: bool
    is_staff: bool
    is_superuser: bool
    group_ids: tuple[int, ...]
    permissions: tuple[str, ...]
    inherited_permissions: tuple[str, ...]
    date_joined: datetime
    mfa_enabled: bool


class UserValues(BaseModel):
    """What to write to a user; only the fields that are set (``model_fields_set``) go.

    The password is write-only: a ``SecretStr``, shown as ``**********`` by every repr. An
    ``email`` of ``None`` removes the address.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    username: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    password: SecretStr | None = None
    is_active: bool | None = None
    is_staff: bool | None = None
    is_superuser: bool | None = None
    group_ids: tuple[int, ...] | None = None
    permissions: tuple[str, ...] | None = None


class GroupRecord(BaseModel):
    """A group as the port reads it: its name and the permission codenames it grants."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    permissions: tuple[str, ...]


class GroupValues(BaseModel):
    """What to write to a group; only the fields that are set go."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    permissions: tuple[str, ...] | None = None


class AppConfigRecord(BaseModel):
    """The application configuration as the port reads it.

    ``settings`` holds the settings that have a value (null ones follow the server's
    environment). The API keys are not in it; ``keys_configured`` names those that hold a value.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    externally_configured_variables: tuple[str, ...]
    settings: dict[str, JsonValue]
    keys_configured: tuple[str, ...]


class ConfigValues(BaseModel):
    """What to write to the configuration: settings (null resets one) and write-only keys."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    settings: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    secrets: dict[str, SecretStr] = Field(default_factory=dict[str, SecretStr])


class TaskRow(BaseModel):
    """A background task with the database id that acknowledging needs."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: Annotated[int, Field(description="Database id: what acknowledge_tasks takes.")]
    task_id: Annotated[str, Field(description="Celery UUID: what get_task takes.")]
    task_type: str
    trigger_source: Annotated[
        str,
        Field(
            description=(
                "scheduled, web_ui, api_upload, folder_consume, email_consume, system or manual."
            )
        ),
    ]
    state: Annotated[TaskState, Field(description="pending, started, success, failure or revoked.")]
    created: datetime
    started: datetime | None
    finished: Annotated[datetime | None, Field(description="Null while the task is running.")]
    duration_seconds: float | None
    document_ids: Annotated[tuple[int, ...], Field(description="Documents the task touched.")]
    acknowledged: bool
    message: Annotated[str | None, Field(description="Why the task failed; null otherwise.")]


class TaskRowPage(BaseModel):
    """One page of tasks as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    rows: tuple[TaskRow, ...]


class ArchiveFile(BaseModel):
    """A ZIP archive as the port reads it: empty and ``truncated`` if it is over the limit.

    ``timed_out`` tells a truncation by time (the archive was not finished) from one by size.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    content: bytes
    truncated: bool
    timed_out: bool = False


# What the tools return.


class UserDetail(BaseModel):
    """A user: names, status flags, groups and permission codenames. Never a password."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    username: str
    email: str | None
    first_name: str
    last_name: str
    is_active: bool
    is_staff: Annotated[
        bool,
        Field(description="Staff users may sign in to the Django admin site and read the logs."),
    ]
    is_superuser: Annotated[
        bool, Field(description="Superusers see and change everything, whatever is granted.")
    ]
    groups: tuple[Principal, ...]
    permissions: Annotated[
        tuple[str, ...], Field(description="Permission codenames granted to the user directly.")
    ]
    inherited_permissions: Annotated[
        tuple[str, ...],
        Field(
            description=(
                "Codenames the user gets from its groups; empty for a superuser, who has every "
                "permission."
            )
        ),
    ]
    date_joined: datetime
    mfa_enabled: bool


class UserOutcome(BaseModel):
    """What a user call did: the user afterwards and whether anything was written."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    user: UserDetail
    changed: Annotated[bool, Field(description="false: the user already was as asked.")]
    message: str


class GroupDetail(BaseModel):
    """A group: its name and the permission codenames it grants to its members."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    permissions: tuple[str, ...]


class GroupOutcome(BaseModel):
    """What a group call did: the group afterwards and whether anything was written."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    group: GroupDetail
    changed: Annotated[bool, Field(description="false: the group already was as asked.")]
    message: str


class Deletion(BaseModel):
    """What a deletion removed."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    message: str


class AppConfig(BaseModel):
    """The application configuration: the settings that have a value, never a key."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    settings: Annotated[
        dict[str, JsonValue],
        Field(description="Settings with a value; the others follow the server's environment."),
    ]
    keys_configured: Annotated[
        tuple[str, ...],
        Field(description="Which API key settings hold a value; the keys are never returned."),
    ]
    externally_configured_variables: Annotated[
        tuple[str, ...],
        Field(description="Environment variables of the server that take part in the setup."),
    ]


class ConfigOutcome(BaseModel):
    """What update_app_config did: the configuration afterwards and what changed."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    config: AppConfig
    changed: Annotated[
        tuple[str, ...], Field(description="The settings that were written, keys included.")
    ]
    message: str


class LogList(BaseModel):
    """The logs this instance keeps."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    logs: tuple[str, ...]


class LogExcerpt(BaseModel):
    """The last lines of a log, oldest first."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    lines: tuple[str, ...]
    truncated: Annotated[
        bool, Field(description="true: older lines were left out to stay within the size limit.")
    ]
    returned_lines: int


class TaskList(BaseModel):
    """One page of tasks, newest first."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many tasks match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    tasks: tuple[TaskRow, ...]


class Acknowledged(BaseModel):
    """How many tasks were acknowledged."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    acknowledged: int
    message: str


class StartedTask(BaseModel):
    """A task that was started by hand."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: Annotated[str, Field(description="Follow it with get_task.")]
    task_type: RunnableTask
    message: str


class ArchiveReport(BaseModel):
    """What a bulk download says about the archive, whether or not the bytes came with it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_ids: tuple[int, ...]
    content: ArchiveContent
    filename: str
    mime_type: str
    size_bytes: int | None
    delivered: Annotated[
        bool, Field(description="The archive is attached as content; false: it is over the limit.")
    ]
    max_bytes: int
    note: str | None


class ArchiveDelivery(BaseModel):
    """A report and, unless the archive is over the size limit, its bytes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    report: ArchiveReport
    data: bytes | None
