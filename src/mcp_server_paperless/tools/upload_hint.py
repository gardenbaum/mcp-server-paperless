"""How uploads by path are enabled: said over stdio while they are off (decision-47).

The operator enables them, never the assistant (decision-7, decision-41). Over HTTP no setting
offers a path, so nothing here is registered there.
"""

from typing import TYPE_CHECKING, override

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from mcp_server_paperless.domain.errors import Validation

if TYPE_CHECKING:
    from fastmcp.tools import ToolResult
    from mcp.types import CallToolRequestParams

PATH_OFF = (
    "Uploading by path is off: the operator, not you, sets PAPERLESS_MCP_UPLOAD_DIRS in the "
    "server's environment (Claude Code plugin: its upload directories option; not on Windows) "
    "and restarts it."
)
PATH_REFUSED = "Uploading by path is not enabled; nothing was uploaded."
UPLOAD_TOOLS = frozenset({"upload_document", "upload_document_version"})


class PathRefusal(Middleware):
    """Answers a call that sends a path to a tool without one, instead of an argument error.

    Only registered where a path could be enabled. The path is neither read nor repeated.
    """

    def __init__(self, tools: frozenset[str]) -> None:
        """Answer for ``tools``, the upload tools this server offers."""
        self._tools: frozenset[str] = tools

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        call = context.message
        if call.name in self._tools and "path" in (call.arguments or {}):
            raise ToolError(Validation(PATH_REFUSED, hint=PATH_OFF).describe())
        return await call_next(context)
