"""Logging: one JSON object per line on stderr, and one record per tool call.

stdout belongs to the MCP stdio transport, so nothing else may write there. Records never carry
document content, tokens or query text at INFO or above: tool arguments, error messages and
exception causes are not logged, and what libraries would print of an exception is replaced by its
type (``private_record``, ``configure_logging``). httpx2/httpcore2 (which log request URLs with
their query at INFO) and sse_starlette (which logs every answer sent as an event at DEBUG) are held
at WARNING whatever the configured level.
"""

import json
import logging
import re
import sys
import threading
import time
import uuid
import warnings
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast, override

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import InputRequiredToolResult

from mcp_server_paperless.domain.diagnostics import error_place, error_types, members
from mcp_server_paperless.domain.errors import Conflict, PaperlessMcpError, Validation

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import Context
    from fastmcp.resources import ResourceResult
    from fastmcp.tools import ToolResult
    from mcp.types import CallToolRequestParams, ReadResourceRequestParams
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Loggers that would put request URLs, and with them search text, into INFO records, or answers
# into DEBUG records.
QUIET_LOGGERS: Final = ("httpx2", "httpcore2", "sse_starlette")

# The id of the tool call being handled; every record logged during the call carries it.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# A request id of the caller that is kept: short and made of characters that are safe in a log.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
REQUEST_ID_HEADER: Final = "x-request-id"
MAX_LOGGED_PATH: Final = 256
# A tool that does not exist is logged by the name the caller gave it.
MAX_LOGGED_NAME: Final = 100

logger = logging.getLogger(__name__)

# How FastMCP's records of a resource that could not be read start; the address follows.
_READING_RESOURCE: Final = "Error reading resource"

# Attributes every LogRecord has; anything else on a record came in through ``extra``.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON line: time, level, logger, message, request id, extras."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        if record.exc_info:
            _make_private(record)  # a record that did not come from the factory
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
        try:
            return json.dumps(payload, default=str)
        except Exception:  # noqa: BLE001 -- a value that cannot be shown
            payload = {"level": record.levelname, "logger": record.name}
            return json.dumps(payload | {"message": unrenderable(record)})


def server_failure(error: BaseException | None) -> str:
    """What may be said of a failed start or stop: no text that can quote Paperless."""
    for member in members(error):  # a task group wraps what a start raises
        if isinstance(member, PaperlessMcpError) and not isinstance(member, Validation | Conflict):
            return member.describe()
    logger.debug("the server failed", exc_info=error)
    return f"The server failed: {error_types(error)}{error_place(error)}."


def private_record(*args: object, **kwargs: object) -> logging.LogRecord:
    """The log record factory: above DEBUG an exception is named by its types and its place only.

    It applies to every logger and handler: messages and tracebacks of libraries quote arguments
    and what Paperless answered (pydantic quotes its input). The original follows at DEBUG. The
    limit: text a library already formatted into its message is not recognised as an error's.
    """
    record = cast("Callable[..., logging.LogRecord]", logging.LogRecord)(*args, **kwargs)
    _make_private(record)
    return record


def _make_private(record: logging.LogRecord) -> None:
    """Take out of ``record`` what can quote a value; never raises."""
    # makeLogRecord asks the factory for a record without a level (typeshed does not say so).
    level = cast("int | None", record.levelno)
    if level is None or level <= logging.DEBUG:
        return
    error = record.exc_info[1] if record.exc_info else None
    try:
        original = record.getMessage()
        changed = _replace_message(record)
        shown = record.getMessage()
    except Exception:  # noqa: BLE001 -- the caller only logs: whatever its values do, say where
        original, changed = "cannot be rendered", False
        shown = unrenderable(record)
    if changed or error is not None:
        logger.debug("original of a record of %s: %s", record.name, original, exc_info=error)
    if error is not None:
        shown += f" ({error_types(error)}{error_place(error)})"
    if changed or error is not None or shown.startswith(unrenderable(record)):
        record.msg, record.args = shown, None
    record.exc_info = record.exc_text = None


def unrenderable(record: logging.LogRecord) -> str:
    """What is logged in place of a record whose message or values cannot be shown."""
    return f"a record of {record.name} at {record.pathname}:{record.lineno} cannot be rendered"


def _replace_message(record: logging.LogRecord) -> bool:
    """Whether ``record`` was changed: exceptions become types."""
    message = cast("object", record.msg)
    if record.name == "asyncio":
        # Its messages hold the repr of tasks, callbacks and their exceptions.
        record.msg, record.args = "error in the event loop", None
    elif str(message).startswith(_READING_RESOURCE):
        # Without the address, which holds the caller's values; CallLogging names the resource.
        record.msg, record.args = _READING_RESOURCE, None
    elif isinstance(message, BaseException):
        record.msg, record.args = error_types(message), None
    elif isinstance(record.args, Mapping):
        if not any(isinstance(value, BaseException) for value in record.args.values()):
            return False
        record.args = {key: _named(value) for key, value in record.args.items()}
    elif any(isinstance(value, BaseException) for value in record.args or ()):
        record.args = tuple(_named(value) for value in record.args or ())
    else:
        return False
    return True


def _named(value: object) -> object:
    return error_types(value) if isinstance(value, BaseException) else value


def _log_warning(
    message: Warning | str, category: type[Warning], filename: str, lineno: int, *_: object
) -> None:
    """Log a Python warning by type and place; its text may quote a value (pydantic's does)."""
    logger.warning("%s at %s:%d", category.__name__, filename, lineno)
    logger.debug("%s", message)


def _log_unhandled(kind: type[BaseException], error: BaseException | None) -> None:
    """Log what Python would print of an error nobody handled."""
    if issubclass(kind, SystemExit):
        return
    if issubclass(kind, KeyboardInterrupt):
        logger.info("stopped by %s", kind.__name__)
    elif error is None:
        logger.critical("unhandled error (%s)", kind.__name__)
    else:
        logger.critical("unhandled error", exc_info=error)


def configure_logging(level: str) -> None:
    """Send every record of ``level`` and above to stderr as JSON, without the text of errors.

    Also what Python would print itself: uncaught exceptions and warnings. The loggers that put
    requests or answers into records are quieted.
    """
    handler = logging.StreamHandler()  # stderr
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.setLogRecordFactory(private_record)
    warnings.showwarning = _log_warning
    sys.excepthook = lambda kind, error, _traceback: _log_unhandled(kind, error)
    threading.excepthook = lambda args: _log_unhandled(args.exc_type, args.exc_value)
    sys.unraisablehook = lambda args: _log_unhandled(args.exc_type, args.exc_value)


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
                extra={
                    "tool": context.message.name[:MAX_LOGGED_NAME],
                    "duration_ms": duration_ms,
                    **outcome,
                },
            )
            request_id.reset(token)

    @override
    async def on_read_resource(
        self,
        context: MiddlewareContext[ReadResourceRequestParams],
        call_next: CallNext[ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        """Log a resource that could not be read by its name, without its address."""
        try:
            return await call_next(context)
        except Exception as error:
            cause = error.__cause__ or error
            logger.info(
                "resource failed",
                extra={"resource": await _resource_name(context), "error": error_types(cause)},
            )
            raise


async def _resource_name(context: MiddlewareContext[ReadResourceRequestParams]) -> str | None:
    """The name of the resource template an address belongs to, None if there is none."""
    server = cast("Context", context.fastmcp_context).fastmcp  # set for every request
    template = await server.get_resource_template(str(context.message.uri))
    return template.name if template else None


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


def _without_traceback(send: Send) -> Send:
    """``send`` for the lifespan: uvicorn logs the message of a failure, Starlette's traceback."""

    async def named(message: Message) -> None:
        if message.get("message"):
            message = {**message, "message": server_failure(sys.exception())}
        await send(message)

    return named


class RequestContext:
    """ASGI middleware: a request id on every request and response, and one log record each.

    The id is the caller's ``X-Request-ID`` if it is short and log-safe, else a new one. The
    record carries method, path, status and duration: no query, headers or body. A start that
    fails is reported without its traceback.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI connection."""
        if scope["type"] == "lifespan":
            await self.app(scope, receive, _without_traceback(send))
            return
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
