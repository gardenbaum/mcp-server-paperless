"""Logging: JSON lines on stderr, one record per tool call, nothing sensitive at INFO or above."""

import asyncio
import json
import logging
import re
import socket
import ssl
import sys
import threading
import warnings
from errno import errorcode
from typing import TYPE_CHECKING, Never, cast, override

import httpx2
import pytest
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.transforms import Transform
from harness import FailsAfterProbe, admin, serve
from mcp import MCPError
from pydantic import ValidationError as PydanticValidationError

from mcp_server_paperless import observability
from mcp_server_paperless.domain.diagnostics import error_place, error_types
from mcp_server_paperless.domain.errors import (
    Ambiguous,
    Conflict,
    PaperlessMcpError,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.observability import (
    RequestContext,
    configure_logging,
    request_id,
    server_failure,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy
from mcp_server_paperless.settings import ServerSettings
from mcp_server_paperless.tools._shared import boundary

if TYPE_CHECKING:
    from collections.abc import Iterator

    from starlette.types import Message, Receive, Scope, Send

pytestmark = pytest.mark.anyio

QUERY = "tax-return-of-jane-doe"
TOKEN = "observability-token-9f8e7d"
CONTENT = "OCR text of a private letter"


@pytest.fixture
def fastmcp_records(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, also for FastMCP's own logger (it does not propagate to the root logger)."""
    fastmcp = logging.getLogger("fastmcp")
    fastmcp.addHandler(caplog.handler)
    yield caplog
    fastmcp.removeHandler(caplog.handler)


def lines(err: str) -> list[dict[str, object]]:
    return [cast("dict[str, object]", json.loads(line)) for line in err.splitlines()]


@pytest.mark.usefixtures("isolated_logging")
def test_records_are_json_lines_on_stderr_with_the_request_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    token = request_id.set("req-1")
    try:
        logging.getLogger("mcp_server_paperless.test").info(
            "hello %s", "world", extra={"tool": "t"}
        )
    finally:
        request_id.reset(token)
    logging.getLogger("mcp_server_paperless.test").debug("below the level")

    captured = capsys.readouterr()
    assert captured.out == ""
    [record] = lines(captured.err)
    assert record["message"] == "hello world"
    assert (record["level"], record["logger"], record["tool"]) == (
        "INFO",
        "mcp_server_paperless.test",
        "t",
    )
    assert record["request_id"] == "req-1"


def failure() -> ValueError:
    """An error of two that both quote content."""
    try:
        try:
            raise KeyError(CONTENT)  # noqa: TRY301 -- the exception to log
        except KeyError as cause:
            raise ValueError(CONTENT) from cause
    except ValueError as error:
        return error


HERE = r"at [\w.]*test_observability:\d+ in failure"


@pytest.mark.usefixtures("isolated_logging")
@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_an_exception_is_logged_by_types_and_place_and_as_a_whole_only_at_debug(
    capsys: pytest.CaptureFixture[str], level: str
) -> None:
    configure_logging(level)

    logging.getLogger("any.library").error("failed for %s", "x", exc_info=failure())

    *whole, record = lines(capsys.readouterr().err)
    assert re.fullmatch(
        rf"failed for x \(ValueError from KeyError {HERE}\)", str(record["message"])
    )
    assert record["level"] == "ERROR"
    assert "exc_info" not in record
    assert len(whole) == (level == "DEBUG")
    for original in whole:
        assert original["level"] == "DEBUG"
        assert original["message"] == "original of a record of any.library: failed for x"
        assert f"ValueError: {CONTENT}" in str(original["exc_info"])


def test_the_types_of_groups_and_cycles_are_named_once() -> None:
    cause, other = KeyError(CONTENT), TypeError(CONTENT)
    cause.__context__ = other
    other.__context__ = cause
    group = ExceptionGroup(CONTENT, [cause, OSError(CONTENT), OSError(48, CONTENT)])

    named = error_types(group)
    assert named == f"ExceptionGroup[KeyError from TypeError, OSError, OSError({errorcode[48]})]"
    assert error_types(None) == ""
    assert error_place(group) == ""


def test_an_error_that_is_odd_is_named_all_the_same() -> None:
    deep = ValueError(CONTENT)
    for _ in range(5000):
        deep, deep.__cause__ = ValueError(CONTENT), deep
    nested: ExceptionGroup[Exception] = ExceptionGroup(CONTENT, [ValueError(CONTENT)])
    for _ in range(5000):
        nested = ExceptionGroup(CONTENT, [nested])
    odd = OSError()
    odd.errno = cast("int", cast("object", []))  # cannot be looked up

    assert error_types(odd) == "OSError"
    assert error_types(nested) == "ExceptionGroup"
    assert error_place(deep) == ""
    # These modules number their errors differently.
    assert error_types(ssl.SSLError(1, CONTENT)) == "SSLError"
    assert error_types(socket.gaierror(8, CONTENT)) == "gaierror"
    assert error_types(socket.herror(1, CONTENT)) == "herror"


def test_the_place_is_the_innermost_frame_and_the_innermost_of_this_package() -> None:
    with pytest.raises(PydanticValidationError) as caught:  # only frames of others
        _ = ServerSettings.model_validate({"max_upload_bytes": CONTENT})
    assert error_place(caught.value).startswith(" at pydantic.")

    async def fails() -> None:
        raise failure()

    async def call() -> None:
        _ = await boundary(fails)()

    with pytest.raises(ToolError) as refused:
        asyncio.run(call())
    grouped = ExceptionGroup(CONTENT, [refused.value])
    # Where it happened, then where this package called that.
    assert re.fullmatch(
        rf" {HERE}, called from mcp_server_paperless\.tools\.confirmation:\d+ in _run",
        error_place(grouped),
    )
    assert error_place(ValueError(CONTENT)) == ""
    assert error_place(None) == ""


@pytest.mark.usefixtures("isolated_logging")
@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_messages_that_hold_values_are_logged_without_them(
    capsys: pytest.CaptureFixture[str], level: str
) -> None:
    configure_logging(level)

    logging.getLogger("asyncio").error(
        "Task exception was never retrieved\nfuture: %s", CONTENT, exc_info=failure()
    )
    library = logging.getLogger("any.library")
    library.error("Error reading resource 'x://%s'", CONTENT)
    library.error("failed: %s, %s", "x", ValueError(CONTENT))
    library.error("failed: %(error)s", {"error": ValueError(CONTENT)})
    library.error("failed: %s", [{"errors": (ValueError(CONTENT), "x")}])
    library.error(failure())
    library.error("no error here", exc_info=True)
    library.error("%(a)s", {"a": "b"})

    records = lines(capsys.readouterr().err)
    above = [str(r["message"]) for r in records if r["level"] == "ERROR"]
    assert re.fullmatch(
        rf"error in the event loop \(ValueError from KeyError {HERE}\)", above.pop(0)
    )
    assert above == [
        "Error reading resource",
        "failed: x, ValueError",
        "failed: ValueError",
        "failed: ({'errors': ('ValueError', 'x')},)",
        "ValueError from KeyError",
        "no error here",
        "b",
    ]
    originals = [str(r["message"]) for r in records if r["level"] == "DEBUG"]
    assert [CONTENT in line for line in originals] == ([True] * 6 if level == "DEBUG" else [])


class Broken:
    """A value that cannot be shown."""

    @override
    def __str__(self) -> str:
        raise RuntimeError(CONTENT)

    @override
    def __repr__(self) -> str:
        raise RuntimeError(CONTENT)


@pytest.mark.usefixtures("isolated_logging")
@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_a_record_that_cannot_be_rendered_is_named_and_breaks_nothing(
    capsys: pytest.CaptureFixture[str], level: str
) -> None:
    configure_logging(level)
    library = logging.getLogger("any.library")

    library.error("%d", CONTENT)
    library.error(Broken())
    library.error("failed: %s", Broken(), exc_info=failure())
    library.error("fine", extra={"shown": Broken()})

    captured = capsys.readouterr().err
    assert CONTENT not in captured.replace(f"ValueError: {CONTENT}", "").replace(
        f"KeyError: '{CONTENT}'", ""
    )
    above = [r for r in lines(captured) if r["level"] == "ERROR"]
    place = r"a record of any.library at .*test_observability.py:\d+ cannot be rendered"
    assert len(above) == 4
    for record in above:
        assert re.match(place, str(record["message"]))
        assert record["logger"] == "any.library"
    assert "ValueError from KeyError" in str(above[2]["message"])


@pytest.mark.usefixtures("isolated_logging")
def test_the_json_formatter_cleans_a_rebuilt_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    error = failure()
    assert logging.makeLogRecord({}).levelno is None

    rebuilt = logging.makeLogRecord(
        {
            "name": "any.library",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "failed",
            "exc_info": (type(error), error, error.__traceback__),
        }
    )
    logging.getLogger().handle(rebuilt)
    logging.getLogger().handle(
        logging.makeLogRecord({"levelno": logging.ERROR, "msg": "bare", "exc_info": True})
    )

    record, bare = lines(capsys.readouterr().err)
    assert bare["message"] == "bare"
    assert str(record["message"]).startswith("failed (ValueError from KeyError")
    assert "exc_info" not in record


class Unraisable:
    """What ``sys.unraisablehook`` gets."""

    def __init__(self, kind: type[BaseException], error: BaseException | None) -> None:
        self.exc_type: type[BaseException] = kind
        self.exc_value: BaseException | None = error


def unraisable(kind: type[BaseException], error: BaseException | None) -> None:
    sys.unraisablehook(cast("sys.UnraisableHookArgs", cast("object", Unraisable(kind, error))))


def failing() -> None:
    raise failure()


@pytest.mark.usefixtures("isolated_logging")
@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_what_python_prints_itself_is_logged_by_type(
    capsys: pytest.CaptureFixture[str], level: str
) -> None:
    configure_logging(level)

    sys.excepthook(ValueError, failure(), None)
    unraisable(ValueError, failure())
    unraisable(ValueError, None)
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(CONTENT), None)
    group = BaseExceptionGroup(CONTENT, [KeyboardInterrupt(CONTENT), SystemExit(CONTENT)])
    sys.excepthook(BaseExceptionGroup, group, None)
    mixed = BaseExceptionGroup(CONTENT, [KeyboardInterrupt(CONTENT), failure()])
    sys.excepthook(BaseExceptionGroup, mixed, None)
    for target in (failing, sys.exit):
        thread = threading.Thread(target=target)
        thread.start()
        thread.join()
    warnings.showwarning(UserWarning(CONTENT), UserWarning, "file.py", 3)

    records = lines(capsys.readouterr().err)
    above = [(r["level"], str(r["message"])) for r in records if r["level"] != "DEBUG"]
    levels = [*["CRITICAL"] * 3, "INFO", "INFO", *["CRITICAL"] * 2, "WARNING"]
    assert [shown for shown, _ in above] == levels
    messages = [message for _, message in above]
    for index in (0, 1, 6):
        assert messages[index].startswith("unhandled error (ValueError from KeyError at ")
    assert messages[2] == "unhandled error (ValueError)"
    assert messages[3] == "stopped by KeyboardInterrupt"
    assert messages[4] == "stopped by BaseExceptionGroup"
    assert "BaseExceptionGroup[KeyboardInterrupt, ValueError from KeyError]" in messages[5]
    assert messages[7] == "UserWarning at file.py:3"
    assert CONTENT not in str(above)
    assert (CONTENT in str(records)) == (level == "DEBUG")


def test_a_failed_start_is_told_by_the_fixed_text_or_by_type() -> None:
    down = UpstreamUnavailable("Down.", hint="Wait.")
    assert server_failure(down) == "Down. Hint: Wait."
    assert server_failure(Validation(CONTENT)) == "The server failed: Validation."
    assert server_failure(Ambiguous(CONTENT)) == "The server failed: Ambiguous."
    assert server_failure(PaperlessMcpError("HTTP 418.")) == "HTTP 418."
    assert server_failure(Conflict(CONTENT)) == "The server failed: Conflict."
    named = server_failure(failure())
    assert re.fullmatch(rf"The server failed: ValueError from KeyError {HERE}\.", named)

    # What a task group raises around the error of a start, also when it is not the only one.
    group = ExceptionGroup(CONTENT, [ValueError(CONTENT), ExceptionGroup(CONTENT, [down])])
    assert server_failure(group) == "Down. Hint: Wait."
    assert server_failure(ExceptionGroup(CONTENT, [Validation(CONTENT)])) == (
        "The server failed: ExceptionGroup[Validation]."
    )


def test_a_failed_start_is_logged_as_a_whole_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    _ = server_failure(Validation(CONTENT))

    [record] = caplog.records
    assert record.levelno == logging.DEBUG
    assert CONTENT in caplog.text


async def request_with_query() -> None:
    def ok(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={}, request=request)

    async with httpx2.AsyncClient(
        base_url="http://paperless.test", transport=httpx2.MockTransport(ok)
    ) as http:
        client = PaperlessClient(
            http, StaticTokenProvider(TOKEN), retry=RetryPolicy(), upload_timeout=1.0
        )
        _ = await client.request("GET", "/api/documents/", params={"query": QUERY})


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
@pytest.mark.usefixtures("isolated_logging")
async def test_query_values_and_tokens_never_reach_info_records(
    capsys: pytest.CaptureFixture[str], level: str
) -> None:
    configure_logging(level)

    await request_with_query()

    important = [r for r in lines(capsys.readouterr().err) if r["level"] != "DEBUG"]
    assert not [r for r in important if QUERY in str(r) or TOKEN in str(r)]


@pytest.mark.usefixtures("isolated_logging")
async def test_without_the_quieted_http_loggers_the_query_would_be_logged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Proves the test above can see what httpx2 logs."""
    configure_logging("INFO")
    logging.getLogger("httpx2").setLevel(logging.INFO)

    await request_with_query()

    assert QUERY in capsys.readouterr().err


@pytest.mark.usefixtures("isolated_logging")
async def test_each_tool_call_is_logged_with_request_id_tool_duration_and_outcome(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")

    async with Client(serve(admin())) as client:
        _ = await client.call_tool("get_instance_overview")
        _ = await client.call_tool("get_instance_overview")

    calls = [r for r in lines(capsys.readouterr().err) if r["message"] == "tool call"]
    assert [(r["tool"], r["outcome"]) for r in calls] == [
        ("get_instance_overview", "ok"),
        ("get_instance_overview", "ok"),
    ]
    assert all(cast("float", r["duration_ms"]) >= 0 for r in calls)
    assert len({r["request_id"] for r in calls}) == 2


async def test_a_failed_call_logs_the_error_type_but_neither_message_nor_cause(
    fastmcp_records: pytest.LogCaptureFixture,
) -> None:
    fastmcp_records.set_level(logging.INFO)
    error = UpstreamUnavailable(f"Paperless failed for {QUERY}")
    error.__cause__ = ValueError(CONTENT)  # what a pydantic cause can carry

    async with Client(serve(FailsAfterProbe(error))) as client:
        result = await client.call_tool("get_instance_overview", raise_on_error=False)

    assert result.is_error
    [call] = [r for r in fastmcp_records.records if r.getMessage() == "tool call"]
    assert (vars(call)["outcome"], vars(call)["error"]) == ("error", "UpstreamUnavailable")
    logged = fastmcp_records.text + str([vars(r) for r in fastmcp_records.records])
    assert CONTENT not in logged
    assert QUERY not in logged


async def test_a_failing_bug_logs_its_type_but_never_its_message_above_debug(
    fastmcp_records: pytest.LogCaptureFixture,
) -> None:
    fastmcp_records.set_level(logging.INFO)

    async with Client(serve(FailsAfterProbe(KeyError(CONTENT)))) as client:
        result = await client.call_tool("get_instance_overview", raise_on_error=False)

    assert result.is_error
    logged = fastmcp_records.text + str([vars(r) for r in fastmcp_records.records])
    assert CONTENT not in logged
    assert CONTENT not in str(result.content)
    assert "get_instance_overview failed: KeyError" in fastmcp_records.messages


async def test_the_traceback_of_a_bug_is_logged_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="mcp_server_paperless.tools._shared")

    async with Client(serve(FailsAfterProbe(KeyError(CONTENT)))) as client:
        _ = await client.call_tool("get_instance_overview", raise_on_error=False)

    [traceback] = [r for r in caplog.records if r.levelno == logging.DEBUG and r.exc_info]
    assert CONTENT in caplog.text
    assert traceback.getMessage() == "get_instance_overview failed"


async def test_a_failed_lifespan_is_reported_without_its_traceback() -> None:
    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "lifespan.startup.complete"})
        try:
            raise failure()
        except ValueError:
            await send({"type": "lifespan.shutdown.failed", "message": CONTENT})

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        raise NotImplementedError

    await RequestContext(app)({"type": "lifespan"}, receive, send)

    complete, failed = sent
    assert complete == {"type": "lifespan.startup.complete"}
    assert re.fullmatch(
        rf"The server failed: ValueError from KeyError {HERE}\.",
        str(cast("object", failed["message"])),
    )


async def test_the_name_of_an_unknown_tool_is_logged_shortened(
    fastmcp_records: pytest.LogCaptureFixture,
) -> None:
    fastmcp_records.set_level(logging.INFO)

    async with Client(serve(admin())) as client:
        _ = await client.call_tool("x" * 500, raise_on_error=False)

    [call] = [r for r in fastmcp_records.records if r.getMessage() == "tool call"]
    assert vars(call)["tool"] == "x" * 100


@pytest.mark.parametrize(
    ("address", "name"),
    [
        (f"paperless://documents/{QUERY}", "document"),
        (f"paperless://documents/7/content?offset={QUERY}", "document_content"),
        ("paperless://documents/1/content?offset=-1", "document_content"),
        (f"paperless://{QUERY}", None),
    ],
)
async def test_a_resource_that_cannot_be_read_is_named_without_its_address(
    fastmcp_records: pytest.LogCaptureFixture, address: str, name: str | None
) -> None:
    fastmcp_records.set_level(logging.INFO)

    async with Client(serve(admin())) as client:
        with pytest.raises(MCPError):
            _ = await client.read_resource(address)

    [record] = [r for r in fastmcp_records.records if r.name == observability.__name__]
    assert record.levelno == logging.INFO
    assert record.getMessage().startswith(f"{name or 'unknown resource'} failed: ")
    assert QUERY not in str(vars(record))


@pytest.mark.parametrize("broken", [True, False])
async def test_the_framework_logs_a_handled_resource_error_only_at_debug(
    fastmcp_records: pytest.LogCaptureFixture, *, broken: bool
) -> None:
    error = KeyError(CONTENT) if broken else Validation(CONTENT)
    fastmcp_records.set_level(logging.INFO, logger="mcp_server_paperless")
    fastmcp_records.set_level(logging.DEBUG, logger="fastmcp")

    class Fails(FakePaperless):
        @override
        async def get_document(self, document_id: int) -> Never:
            raise error

    documents = Fails(seeded(), user="admin")
    async with Client(serve(admin(), documents=documents)) as client:
        with pytest.raises(MCPError):
            _ = await client.read_resource("paperless://documents/1")

    [own] = [r for r in fastmcp_records.records if r.name == "fastmcp.server.server"]
    assert own.levelno == logging.DEBUG
    [failed] = [r for r in fastmcp_records.records if r.name == observability.__name__]
    assert failed.getMessage() == f"document failed: {type(error).__name__}"
    assert failed.levelno == (logging.ERROR if broken else logging.INFO)
    place = r"\S*test_observability:\d+ in get_document, called from "
    place += r"mcp_server_paperless\.services\.documents:\d+ in \w+"
    assert re.fullmatch(place, str(cast("object", vars(failed)["at"])))


async def test_the_failure_of_a_resource_carries_a_request_id(
    capsys: pytest.CaptureFixture[str], isolated_logging: None
) -> None:
    _ = isolated_logging
    configure_logging("INFO")

    async with Client(serve(admin())) as client:
        with pytest.raises(MCPError):
            _ = await client.read_resource("paperless://documents/999")

    [record] = [r for r in lines(capsys.readouterr().err) if "document failed" in str(r)]
    assert record["request_id"]


class NoTemplates(Transform):
    """A catalogue that cannot look a template up."""

    @override
    async def get_resource_template(self, *_: object, **__: object) -> Never:
        raise RuntimeError(CONTENT)


async def test_a_lookup_that_fails_does_not_replace_the_error(
    fastmcp_records: pytest.LogCaptureFixture,
) -> None:
    fastmcp_records.set_level(logging.INFO)
    server = serve(admin())

    async with Client(server) as client:
        server.add_transform(NoTemplates())
        with pytest.raises(MCPError):
            _ = await client.read_resource("paperless://documents/999")

    [record] = [r for r in fastmcp_records.records if r.name == observability.__name__]
    assert record.getMessage() == "unknown resource failed: RuntimeError"


async def test_a_bug_in_a_tool_is_logged_with_its_place(
    fastmcp_records: pytest.LogCaptureFixture,
) -> None:
    fastmcp_records.set_level(logging.INFO)

    async with Client(serve(FailsAfterProbe(KeyError(CONTENT)))) as client:
        _ = await client.call_tool("get_instance_overview", raise_on_error=False)

    [record] = [r for r in fastmcp_records.records if "overview failed" in r.getMessage()]
    assert " in get_overview, called from mcp_server_paperless.services.instance:" in str(
        cast("object", vars(record)["at"])
    )
