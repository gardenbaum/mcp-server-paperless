"""Raw read access to the Paperless API: one GET under /api/ (decision-25).

The caller chooses the request target, so the checks here are the security core of the raw
toolset. The path is matched against an allow-list (ASCII letters, digits, ``_`` and ``-`` in
segments below ``/api/``), never repaired or decoded; endpoints that hold secrets or files are
denied; the answer must be JSON, has secret-looking values replaced and is cut at a size cap.
"""

import json
import re
from typing import TYPE_CHECKING, Final, NoReturn, cast
from urllib.parse import parse_qs, urlsplit

from mcp_server_paperless.domain.errors import PaperlessMcpError, Validation
from mcp_server_paperless.domain.raw import RawApiResult
from mcp_server_paperless.domain.urls import shown_url, text_without_credentials

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from mcp_server_paperless.domain.ports import RawReader
    from mcp_server_paperless.domain.raw import RawReply

# The most bytes of one answer that are read and parsed; a longer one is reported, not kept.
READ_LIMIT: Final = 1024 * 1024
# The most characters of JSON text one result carries (the size of a content slice).
MAX_OUTPUT: Final = 20_000
MAX_PATH: Final = 200
MAX_SEGMENTS: Final = 8
MAX_PARAMS: Final = 30
MAX_PARAM_NAME: Final = 64
MAX_PARAM_VALUE: Final = 2000
REDACTED: Final = "[redacted]"

# /api/ and segments of ASCII letters, digits, "_" and "-", slash-separated, with
# an optional trailing slash. No "%": Paperless paths need no percent-encoding, so an encoded
# character is refused whole instead of being decoded and checked.
_PATH = re.compile(r"/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?")
_PARAM_NAME = re.compile(rf"[A-Za-z0-9_.-]{{1,{MAX_PARAM_NAME}}}")
_PLAIN_PATHS: Final = (
    "only /api/ followed by names of letters, digits, _ and - is allowed, with no query, "
    "no encoded characters and no dot segments"
)
_EXAMPLE = "/api/tags/"

# Segments that mark an endpoint with secrets (what the answer holds), with the way to the same
# information that does not leak it.
_SECRET_SEGMENTS: Final = {
    "profile": (
        "The profile holds the API token and the TOTP secret. "
        "get_instance_overview names the current user."
    ),
    "config": (
        "The application configuration holds API keys. "
        "get_instance_overview shows the version and the status."
    ),
    "mail_accounts": (
        "Mail accounts hold passwords. "
        "The mail toolset has list_mail_accounts and get_mail_account."
    ),
    "token": "Tokens are issued by login flows, which this server does not offer.",
    "oauth": "The OAuth callbacks are login flows that change state; they are not offered.",
}
# Segments that mark a file download; the curated tools return files with size and content rules.
_FILE_SEGMENTS: Final = frozenset({"download", "preview", "thumb", "bulk_download"})
# Segments of endpoints that ask a language model: the ai toolset has the tool, with its cost
# warning and its switch.
_AI_SEGMENTS: Final = frozenset({"ai_suggestions"})
_AI_HINT: Final = (
    "get_ai_suggestions (ai toolset) does this; each call may cost money and sends the "
    "document text to the model backend."
)
_FILE_HINT: Final = (
    "Files are returned by download_document, get_document_pages and get_document_thumbnail, which "
    "cap their size."
)
# Parts of a JSON key that mark a secret value, and the key of webhook headers (they carry the
# authorization of the receiving service).
_SECRET_KEY_PARTS: Final = (
    *("password", "secret", "api_key", "apikey", "auth_token", "token", "private_key"),
)
# Keys that hold webhook settings (headers and params carry tokens, the body can).
_SECRET_KEYS: Final = frozenset({"headers", "params", "body"})
# Names that contain a secret word but are flags or dates.
_NOT_SECRET_KEYS: Final = frozenset({"is_token", "has_usable_password", "token_expires"})
_PAGE_LINKS: Final = frozenset({"next", "previous"})


def _refuse(reason: str) -> NoReturn:
    message = f"The path is refused: {reason}."
    hint = (
        f"Use a relative path under /api/ such as {_EXAMPLE} or /api/documents/12/history/; "
        "put filters in params, not in the path."
    )
    raise Validation(message, hint=hint)


def check_path(path: str) -> str:
    """The path if it is an allowed endpoint of the Paperless API; never repaired or decoded.

    Raises:
        Validation: The path is not ``/api/`` followed by plain segments, or names an endpoint
            with secrets or files. The message never repeats the text of a refused path.
    """
    if len(path) > MAX_PATH:
        _refuse(f"it is longer than {MAX_PATH} characters")
    if _PATH.fullmatch(path) is None:
        _refuse(_PLAIN_PATHS)
    segments = [segment.lower() for segment in path.removeprefix("/api/").split("/") if segment]
    if len(segments) > MAX_SEGMENTS:
        _refuse(f"it has more than {MAX_SEGMENTS} segments")
    for segment in segments:
        if segment in _SECRET_SEGMENTS:
            message = f"Reading /api/.../{segment}/ is not available: it holds secrets."
            raise Validation(message, hint=_SECRET_SEGMENTS[segment])
        if segment in _AI_SEGMENTS:
            message = f"Reading /api/.../{segment}/ is not available: it asks a language model."
            raise Validation(message, hint=_AI_HINT)
        if segment in _FILE_SEGMENTS:
            message = f"Reading /api/.../{segment}/ is not available: it returns a file."
            raise Validation(message, hint=_FILE_HINT)
    return path


def check_params(params: Mapping[str, str | int | bool] | None) -> dict[str, str]:
    """The query parameters as text.

    Raises:
        Validation: A name or a value is not allowed, or there are too many.
    """
    if not params:
        return {}
    if len(params) > MAX_PARAMS:
        message = f"There are more than {MAX_PARAMS} params."
        raise Validation(message, hint="Send fewer filters.")
    checked: dict[str, str] = {}
    for name, value in params.items():
        if name.lower() == "format":
            message = "The param format is refused: Paperless answers it with a misleading error."
            raise Validation(message, hint="Answers are JSON already; leave format out.")
        if _PARAM_NAME.fullmatch(name) is None:
            message = (
                f"A param name is refused: names have 1 to {MAX_PARAM_NAME} letters, digits, "
                "_, - or ."
            )
            hint = "Use the names of the Paperless API, such as page_size or ordering."
            raise Validation(message, hint=hint)
        text = ("true" if value else "false") if isinstance(value, bool) else str(value)
        if len(text) > MAX_PARAM_VALUE or not text.isprintable():
            message = (
                f"The value of param {name} is refused: at most {MAX_PARAM_VALUE} characters "
                "and no control characters or new lines."
            )
            raise Validation(message, hint="Shorten the value.")
        checked[name] = text
    return checked


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    if lowered in _NOT_SECRET_KEYS:
        return False
    return lowered in _SECRET_KEYS or any(part in lowered for part in _SECRET_KEY_PARTS)


def _page_number(link: str) -> int | None:
    """The page a ``next``/``previous`` link points to, so no host leaks (decision-7)."""
    pages = parse_qs(urlsplit(link).query).get("page")
    if pages is None:
        return 1
    try:
        return int(pages[0])
    except ValueError:
        return None


def redact(value: JsonValue) -> tuple[JsonValue, int]:
    """``value`` with secret values replaced and page links reduced to page numbers.

    Returns the cleaned value and how many secret values were replaced.
    """
    if isinstance(value, dict):
        cleaned: dict[str, JsonValue] = {}
        count = 0
        for key, item in value.items():
            if key in _PAGE_LINKS and isinstance(item, str):
                cleaned[key] = _page_number(item)
            elif item is not None and _is_secret(key):
                cleaned[key] = REDACTED
                count += 1
            else:
                cleaned[key], inner = redact(item)
                count += inner
        return cleaned, count
    if isinstance(value, list):
        rows = [redact(item) for item in value]
        return [row for row, _ in rows], sum(count for _, count in rows)
    return value, 0


def _url_hidden(value: JsonValue, *, in_webhook: bool = False) -> tuple[JsonValue, int]:
    """``value`` with the URLs of webhooks without credentials, and how many had some."""
    if isinstance(value, dict):
        cleaned: dict[str, JsonValue] = {}
        count = 0
        for key, item in value.items():
            if in_webhook and key == "url" and isinstance(item, str):
                cleaned[key] = shown_url(item)
                count += cleaned[key] != item
            else:
                cleaned[key], inner = _url_hidden(item, in_webhook=key == "webhook")
                count += inner
        return cleaned, count
    if isinstance(value, list):
        rows = [_url_hidden(item, in_webhook=in_webhook) for item in value]
        return [row for row, _ in rows], sum(count for _, count in rows)
    return value, 0


def _lines_hidden(value: JsonValue) -> tuple[JsonValue, int]:
    """The lines of a log without the credentials of URLs, and how many had some."""
    lines = value if isinstance(value, list) else [value]
    cleaned: list[JsonValue] = [
        text_without_credentials(li) if isinstance(li, str) else li for li in lines
    ]
    count = sum(new != old for new, old in zip(cleaned, lines, strict=True))
    return (cleaned if isinstance(value, list) else cleaned[0]), count


def _is_json(content_type: str) -> bool:
    return content_type.partition(";")[0].strip().lower() == "application/json"


class RawApiService:
    """Reads any allowed path of the Paperless API on behalf of the current user."""

    def __init__(self, reader: RawReader) -> None:
        """Read through ``reader``."""
        self._reader: RawReader = reader

    async def get(self, path: str, params: Mapping[str, str | int | bool] | None) -> RawApiResult:
        """GET ``path`` with the query ``params``.

        Raises:
            Validation: The path or a param is refused (nothing was sent), or Paperless rejects
                the request.
            NotFound: No such endpoint or object, or not visible to the user.
            PermissionDenied: The user may not read it.
            PaperlessMcpError: The answer is not JSON, or Paperless redirected the request or
                answered with an error.
        """
        checked = check_path(path)
        query = check_params(params)
        reply = await self._reader.get(checked, query, max_bytes=READ_LIMIT)
        return _result(checked, reply)


def _result(path: str, reply: RawReply) -> RawApiResult:
    """The result for an answer: parsed and redacted JSON, cut at ``MAX_OUTPUT``."""
    if reply.truncated:
        return _cut(path, reply, text=None, redacted=0)
    if not reply.body:
        return RawApiResult(
            path=path,
            status=reply.status,
            data=None,
            truncated=False,
            text=None,
            redacted=0,
            note=None,
        )
    if not _is_json(reply.content_type):
        message = f"Paperless answered {path} with something other than JSON; it is not returned."
        raise PaperlessMcpError(message, hint=_FILE_HINT)
    try:
        parsed = cast("JsonValue", json.loads(reply.body))
    except ValueError:
        message = f"Paperless sent an unexpected response for GET {path}."
        raise PaperlessMcpError(message) from None
    is_log = path.lower().startswith("/api/logs/")
    parsed, urls = _lines_hidden(parsed) if is_log else _url_hidden(parsed)
    data, redacted = redact(parsed)
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    if len(text) > MAX_OUTPUT:
        return _cut(path, reply, text=text[:MAX_OUTPUT], redacted=redacted, urls=urls)
    return RawApiResult(
        path=path,
        status=reply.status,
        data=data,
        truncated=False,
        text=None,
        redacted=redacted + urls,
        note=_redaction_note(redacted, urls),
    )


def _redaction_note(redacted: int, urls: int) -> str | None:
    notes = [f"{redacted} secret value(s) were replaced with {REDACTED}."] if redacted else []
    if urls:
        notes.append(f"{urls} URL(s) or log line(s) are shown without credentials.")
    return " ".join(notes) or None


def _cut(
    path: str, reply: RawReply, *, text: str | None, redacted: int, urls: int = 0
) -> RawApiResult:
    """The result for an answer that is too long: its first part, or nothing if unreadable."""
    narrowing = (
        "The answer is too long to return whole. Narrow it with params: page_size, page, "
        "fields, ordering or filters of the endpoint."
    )
    secrets = _redaction_note(redacted, urls)
    return RawApiResult(
        path=path,
        status=reply.status,
        data=None,
        truncated=True,
        text=text,
        redacted=redacted + urls,
        note=f"{narrowing} {secrets}" if secrets else narrowing,
    )
