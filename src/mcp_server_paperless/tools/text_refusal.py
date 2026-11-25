"""Text that cannot be sent is refused before anything else happens to a call (PLMCP-118)."""

from typing import TYPE_CHECKING, override

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.domain.texts import REFUSED_TEXT, refused_text

if TYPE_CHECKING:
    from fastmcp.tools import ToolResult
    from mcp.types import CallToolRequestParams


class TextRefusal(Middleware):
    """Answers a call with a lone surrogate anywhere in its arguments, whatever the tool.

    Before the arguments are validated, so a tool or an argument that is added is covered. Says
    where it was and repeats none of the text.
    """

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        arguments = context.message.arguments or {}
        # Over stdio the text was taken out before the call could be parsed (domain/texts.py).
        place = refused_text(arguments) or arguments.get(REFUSED_TEXT)
        if place is not None:
            message = f"The {place} is not valid text."
            hint = (
                f"Give the {place} in plain characters: it holds half of a character (a lone "
                "surrogate)."
            )
            raise ToolError(Validation(message, hint=hint).describe())
        return await call_next(context)
