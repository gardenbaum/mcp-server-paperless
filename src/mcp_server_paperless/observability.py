"""Logging: one JSON object per line on stderr, and one record per tool call.

stdout belongs to the MCP stdio transport, so nothing else may write there. Records never carry
document content, tokens or query text at INFO or above: tool arguments, error messages and
exception causes are not logged, and httpx2/httpcore2 (which log request URLs with their query
at INFO) are held at WARNING whatever the configured level.
"""

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast, override

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import InputRequiredToolResult

if TYPE_CHECKING:
    from fastmcp.tools import ToolResult
    from mcp.types import CallToolRequestParams
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Loggers that would put request URLs, and with them search text, into INFO records.
QUIET_LOGGERS: Final = ("httpx2", "httpcore2")

# The id of the tool call being handled; every record logged during the call carries it.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# A request id of the caller that is kept: short and made of characters that are safe in a log.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
REQUEST_ID_HEADER: Final = "x-request-id"
MAX_LOGGED_PATH: Final = 256

logger = logging.getLogger(__name__)

# Attributes every LogRecord has; anything else on a record came in through ``extra``.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON line: time, level, logger, message, request id, extras."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        current = request_id.get()
        if current is not None:
            payload["request_id"] = current
        extras = cast("dict[str, object]", vars(record))
        payload |= {key: value for key, value in extras.items() if key not in _STANDARD}
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Send every record of ``level`` and above to stderr as JSON; quiet the HTTP loggers."""
    handler = logging.StreamHandler()  # stderr
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class CallLogging(Middleware):
    """Log each tool call with a fresh request id, the tool name, its duration and outcome.

    The outcome is ``ok``, ``error``, or ``asked``: the result is a question to the user, and the
    answer comes with another call (decision-44).
    """

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        token = request_id.set(_http_request_id() or uuid.uuid4().hex)
        started = time.perf_counter()
        outcome: dict[str, object] = {"outcome": "ok"}
        try:
            result = await call_next(context)
        except Exception as error:
            # The boundary raises ToolError from None and FastMCP masks with `from error`:
            # name the original error's type, never its message.
            cause = error.__cause__ or error.__context__ or error
            outcome = {"outcome": "error", "error": type(cause).__name__}
            raise
        else:
            if isinstance(result, InputRequiredToolResult):
                outcome = {"outcome": "asked"}
            return result
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "tool call",
                extra={"tool": context.message.name, "duration_ms": duration_ms, **outcome},
            )
            request_id.reset(token)


def _http_request_id() -> str | None:
    """The id ``RequestContext`` gave the HTTP request this call came in on, if there is one."""
    try:
        request = get_http_request()
    except RuntimeError:
        return None  # stdio or in-process: no HTTP request
    state = cast("dict[str, object]", request.scope.get("state", {}))
    found = state.get("request_id")
    return found if isinstance(found, str) else None


def request_header(scope: Scope, name: bytes) -> bytes | None:
    """The first value of the (lowercase) request header ``name``, or None."""
    headers = cast("list[tuple[bytes, bytes]]", scope["headers"])
    return next((value for key, value in headers if key == name), None)


class RequestContext:
    """ASGI middleware: a request id on every request and response, and one log record each.

    The id is the caller's ``X-Request-ID`` if it is short and log-safe, else a new one. The
    record carries method, path, status and duration: no query, headers or body.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI connection."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = (request_header(scope, b"x-request-id") or b"").decode("latin-1")
        identifier = given if _REQUEST_ID.fullmatch(given) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = identifier
        status = 500
        responded = False

        async def stamped(message: Message) -> None:
            nonlocal status, responded
            if message["type"] == "http.response.start":
                responded = True
                status = cast("int", message["status"])
                sent = cast("list[tuple[bytes, bytes]]", message["headers"])
                headers = [(k, v) for k, v in sent if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", identifier.encode()))
                message = {**message, "headers": headers}
            await send(message)

        token = request_id.set(identifier)
        started = time.perf_counter()
        try:
            try:
                await self.app(scope, receive, stamped)
            except Exception as error:
                logger.error(  # noqa: TRY400 -- by type only: a traceback or message may hold content
                    "unhandled error in a request", extra={"error": type(error).__name__}
                )
                if responded:
                    raise  # the answer began: nothing left to replace
                await stamped({"type": "http.response.start", "status": 500, "headers": []})
                await stamped({"type": "http.response.body", "body": b"Internal Server Error"})
        finally:
            logger.info(
                "http request",
                extra={
                    "method": scope["method"],
                    "path": cast("str", scope["path"])[:MAX_LOGGED_PATH],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "request_id": identifier,
                },
            )
            request_id.reset(token)
