"""What the adapters send and what the tools ask of the ports, seen from the tests (PLMCP-91).

The schema guard records every request an adapter sends (`record`), and the tool tests record,
while `RECORDING` is on, which port method each tool called with which arguments (`recorded`,
`ToolMarker`). `tests/quality/derive.py` joins the two for the coverage table of the Paperless API.
"""

import contextvars
import dataclasses
import inspect
import json
from email.parser import BytesParser
from email.policy import default
from typing import TYPE_CHECKING, NamedTuple, cast, override

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import httpx2
    from fastmcp.tools import ToolResult
    from mcp.types import CallToolRequestParams

    from mcp_server_paperless.server import Ports

type Operation = tuple[str, str]  # ("GET", "/api/tags/{id}/")
type Call = tuple[str, str]  # (port, method)

MAX_VALUE = 80  # longer text is no enum value; it may be a title or a document's text


class Observation(NamedTuple):
    """One request an adapter method sent, and the tool that caused it (None: an adapter test)."""

    tool: str | None
    call: Call
    operation: Operation
    query: frozenset[str]  # parameter names
    body: frozenset[str]  # field paths: `a`, `a.b`, `a[].b`
    values: frozenset[str]  # `query:name=value` and `body:path=value` of short scalars


class PortCall(NamedTuple):
    """A call of a port method while a tool ran, with its arguments."""

    tool: str | None
    field: str  # the field of `Ports`
    method: str
    args: tuple[object, ...]
    kwargs: Mapping[str, object]


OBSERVED: list[Observation] = []
RESPONSES: dict[Operation, tuple[int, dict[str, str], bytes]] = {}  # the replay answers with these
PORT_CALLS: list[PortCall] = []
CURRENT_TOOL: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tool", default=None
)
GIVEN: dict[str, set[str]] = {}  # the arguments the scenarios gave each tool
TOOL_ARGUMENTS: dict[str, frozenset[str]] = {}  # the arguments each tool has
RECORDING = False  # the tool tests record port calls only while the coverage test runs them


def port_method() -> Call | None:
    """The adapter method (as `(port, method)`) the test called, found on the stack.

    It is the outermost frame of an adapter class (`PaperlessTrashReader` implements
    `TrashReader`), so the helpers it calls on the way do not count; the client is no adapter.
    """
    found: Call | None = None
    frame = inspect.currentframe()
    while frame is not None:  # innermost first, so the outermost adapter frame wins
        module = str(cast("Mapping[str, object]", frame.f_globals).get("__name__", ""))
        owner = cast("Mapping[str, object]", frame.f_locals).get("self")
        if (
            module.startswith("mcp_server_paperless.paperless.")
            and not module.endswith(".client")
            and owner is not None
        ):
            found = (type(owner).__name__.removeprefix("Paperless"), frame.f_code.co_name)
        frame = frame.f_back
    return found


def scalar_text(value: object) -> str | None:
    """A short string, number or flag as text (`true`, `3`); anything else is no enum value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | str):
        text = str(value)
        return text if len(text) <= MAX_VALUE else None
    return None


def leaves(value: object, path: str = "") -> tuple[set[str], set[str]]:
    """The field paths of a JSON value and the `path=value` of its short scalars.

    An object's field is `path.name`; a list's items share `path[]`.
    """
    paths: set[str] = set()
    values: set[str] = set()
    if isinstance(value, dict):
        for name, item in cast("dict[str, object]", value).items():
            child = f"{path}.{name}" if path else name
            paths.add(child)
            more_paths, more_values = leaves(item, child)
            paths |= more_paths
            values |= more_values
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            more_paths, more_values = leaves(item, f"{path}[]")
            paths |= more_paths
            values |= more_values
    elif path and (text := scalar_text(value)) is not None:
        values.add(f"{path}={text}")
    return paths, values


def request_fields(
    request: httpx2.Request,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """The query names, the body field paths and the `kind:path=value` texts of a request."""
    items = list(request.url.params.multi_items())
    query = frozenset(name for name, _ in items)
    values = {f"query:{name}={value}" for name, value in items if len(value) <= MAX_VALUE}
    paths: set[str] = set()
    found: set[str] = set()
    if "multipart/" in request.headers.get("Content-Type", ""):
        header = f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
        message = BytesParser(policy=default).parsebytes(header + request.content)
        for part in message.iter_parts():
            name = str(part.get_param("name", header="content-disposition"))
            paths.add(name)
            if part.get_filename() is None:
                text = cast("bytes", part.get_payload(decode=True)).decode(errors="replace")
                found |= {f"{name}={text}"} if len(text) <= MAX_VALUE else set()
    elif request.content:
        try:
            body = cast("object", json.loads(request.content))
        except ValueError:
            body = None
        paths, found = leaves(body)
    return query, frozenset(paths), frozenset(values | {f"body:{item}" for item in found})


def record(request: httpx2.Request, operation: Operation) -> None:
    """Remember a request for the adapter method that sent it (none, for a plain client call)."""
    if (call := port_method()) is not None:
        query, body, values = request_fields(request)
        OBSERVED.append(Observation(CURRENT_TOOL.get(), call, operation, query, body, values))


class Recorder:
    """Stands in for a port and notes every call a tool makes through it."""

    def __init__(self, field: str, port: object) -> None:
        """Wrap `port`, the `Ports` field called `field`."""
        self._field: str = field
        self._port: object = port

    def __getattr__(self, method: str) -> object:
        """The port's attribute; a method of it is noted when called."""
        target = cast("object", getattr(self._port, method))
        if not callable(target):
            return target
        function: Callable[..., object] = target

        def call(*args: object, **kwargs: object) -> object:
            PORT_CALLS.append(PortCall(CURRENT_TOOL.get(), self._field, method, args, kwargs))
            return function(*args, **kwargs)

        return call


def recorded(ports: Ports) -> Ports:
    """`ports` with every port recorded, while `RECORDING` is on."""
    if not RECORDING:
        return ports
    wrapped = {
        f.name: Recorder(f.name, cast("object", getattr(ports, f.name)))
        for f in dataclasses.fields(ports)
    }
    return dataclasses.replace(ports, **wrapped)


class ToolMarker(Middleware):
    """Tells `Recorder` and `record` which tool is running."""

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        token = CURRENT_TOOL.set(context.message.name)
        try:
            return await call_next(context)
        finally:
            CURRENT_TOOL.reset(token)
