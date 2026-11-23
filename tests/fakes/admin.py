r"""What the fake knows about administration: input rules of users, groups and the configuration.

Probed on Paperless 3.4.2 (PLMCP-31): usernames are unique (case-sensitive) and match
``^[\\w.@+-]+$``; an e-mail address must look like one; a user's groups must exist; permissions
are codenames without the app label and an unknown one is a 400; a group has a unique name of at
most 150 characters and needs a ``permissions`` list on creation; the configuration ignores
names it does not know, resets a setting to the server default on ``null`` and answers 400 with
the field's message for a value outside the choices or below the minimum.
"""

import io
import re
import zipfile
from typing import TYPE_CHECKING, Final

from fakes.document_values import reject
from mcp_server_paperless.domain.admin import CONFIG_READ_ONLY, CONFIG_SETTINGS

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from pydantic import JsonValue

# Global permission codenames Paperless offers (the models of the apps documents, paperless,
# paperless_mail and auth that a user may be granted), each with view, add, change and delete.
MODELS: Final = (
    *("document", "tag", "correspondent", "documenttype", "storagepath", "customfield"),
    *("savedview", "uisettings", "user", "group", "mailaccount", "mailrule", "processedmail"),
    *("paperlesstask", "workflow", "workflowtrigger", "workflowaction", "sharelink"),
    *("sharelinkbundle", "note", "applicationconfiguration"),
)
KNOWN_PERMISSIONS: Final = frozenset(
    f"{action}_{model}" for model in MODELS for action in ("view", "add", "change", "delete")
)
CHOICES: Final[dict[str, tuple[str, ...]]] = {
    "output_type": ("pdf", "pdfa", "pdfa-1", "pdfa-2", "pdfa-3"),
    "mode": ("auto", "force", "redo", "off"),
    "archive_file_generation": ("auto", "always", "never"),
    "unpaper_clean": ("clean", "clean-final", "none"),
    "color_conversion_strategy": (
        *("LeaveColorUnchanged", "RGB", "UseDeviceIndependentColor", "Gray", "CMYK"),
    ),
    "remote_ocr_engine": ("azureai",),
    "remote_ocr_mode": ("always", "workflow_only"),
    "llm_embedding_backend": ("openai-like", "huggingface", "ollama"),
    "llm_backend": ("openai-like", "ollama"),
}
MINIMUMS: Final[dict[str, float]] = {
    "pages": 1,
    "image_dpi": 1,
    "rotate_pages_threshold": 0,
    "max_image_pixels": 0,
    "barcode_upscale": 1,
    "barcode_dpi": 1,
    "barcode_max_pages": 1,
    "llm_embedding_chunk_size": 1,
    "llm_context_size": 1,
    "llm_request_timeout": 1,
}
MAX_LENGTHS: Final[dict[str, int]] = {
    "language": 32,
    "app_title": 48,
    "barcode_string": 32,
    "barcode_asn_prefix": 32,
    "remote_ocr_endpoint": 256,
    "llm_embedding_model": 128,
    "llm_embedding_endpoint": 256,
    "llm_model": 128,
    "llm_endpoint": 256,
    "llm_output_language": 32,
}
BOOLEANS: Final = frozenset(
    {"deskew", "rotate_pages", "barcodes_enabled", "barcode_enable_tiff_support"}
    | {"barcode_retain_split_pages", "barcode_enable_asn", "barcode_enable_tag"}
    | {"barcode_tag_split", "barcode_store_values", "ai_enabled"}
)
USERNAME = re.compile(r"^[\w.@+-]+$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_USERNAME = 150


def check_username(username: str, taken: Collection[str]) -> None:
    """Reject a blank, malformed, too long or taken username like Paperless (a 400)."""
    if not username:
        reject("username", "This field may not be blank.")
    if len(username) > MAX_USERNAME:
        reject("username", "Ensure this field has no more than 150 characters.")
    if not USERNAME.match(username):
        reject(
            "username",
            (
                "Enter a valid username. This value may contain only letters, numbers, and "
                "@/./+/-/_ characters."
            ),
        )
    if username in taken:
        reject("username", "A user with that username already exists.")


def check_email(email: str) -> None:
    """Reject an address that does not look like one (a blank one is no address)."""
    if email and not EMAIL.match(email):
        reject("email", "Enter a valid email address.")


def check_permissions(codenames: Collection[str]) -> None:
    """Reject a permission codename Paperless does not know."""
    for codename in codenames:
        if codename not in KNOWN_PERMISSIONS:
            reject("permissions", f"Object with codename={codename} does not exist.")


def check_group_name(name: str, taken: Collection[str]) -> None:
    """Reject a blank, too long or taken group name like Paperless (a 400)."""
    if not name:
        reject("name", "This field may not be blank.")
    if len(name) > MAX_USERNAME:
        reject("name", "Ensure this field has no more than 150 characters.")
    if name in taken:
        reject("name", "group with this name already exists.")


def check_setting(name: str, value: JsonValue) -> None:
    """Reject a value outside the choices, below the minimum or too long, like the config view."""
    if value is None:
        return
    if name in CHOICES and value not in CHOICES[name]:
        reject(name, f'"{value}" is not a valid choice.')
    minimum = MINIMUMS.get(name)
    if minimum is not None and (not isinstance(value, int | float) or value < minimum):
        reject(name, f"Ensure this value is greater than or equal to {minimum:g}.")
    limit = MAX_LENGTHS.get(name)
    if limit is not None and (not isinstance(value, str) or len(value) > limit):
        reject(name, f"Ensure this field has no more than {limit} characters.")
    if name in BOOLEANS and not isinstance(value, bool):
        reject(name, f'"{value}" is not a valid boolean.')


def apply_settings(
    current: Mapping[str, JsonValue], given: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """The settings after ``given``: null resets, unknown names are ignored, bad values fail."""
    changed = dict(current)
    for name, value in given.items():
        if name not in CONFIG_SETTINGS or name in CONFIG_READ_ONLY:
            continue
        check_setting(name, value)
        if value is None:
            _ = changed.pop(name, None)
        else:
            changed[name] = value
    return changed


def archive_of(files: list[tuple[str, bytes]]) -> bytes:
    """A stored ZIP archive of the files, like the bulk download makes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, content in files:
            archive.writestr(name, content)
    return buffer.getvalue()
