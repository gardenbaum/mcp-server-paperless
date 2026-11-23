"""RawReader: a GET of one path under ``/api/``, on the shared client (decision-25).

The URL is built only by the client, which refuses any target but the configured Paperless and
sends the credential of the current user. The path is checked here again, independently of the
service, so the adapter is safe on its own: a relative path under ``/api/`` with plain segments.
Redirects are never followed.
"""

import re
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import PaperlessMcpError, Validation
from mcp_server_paperless.domain.raw import RawReply

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mcp_server_paperless.paperless.client import PaperlessClient

_SAFE_PATH: Final = re.compile(r"/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?")
_REDIRECTS: Final = range(300, 400)


class PaperlessRawReader:
    """``RawReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def get(self, path: str, params: Mapping[str, str], *, max_bytes: int) -> RawReply:
        """See ``RawReader.get``."""
        if _SAFE_PATH.fullmatch(path) is None:
            message = "The adapter refuses a path that is not plain and under /api/."
            raise Validation(message)
        expected = self._client.base_url.path.rstrip("/") + path
        if self._client.target(path, params).path != expected:
            message = "The built URL is not the configured Paperless base and the path."
            raise Validation(message)
        # Never retried: a GET of some endpoints costs money (decision-25).
        found = await self._client.download(path, params=params, max_bytes=max_bytes, retry=False)
        if found.status in _REDIRECTS:
            message = "Paperless redirected the request; redirects are not followed."
            hint = (
                "The path is probably wrong or lacks its trailing slash; "
                "ask for an exact path such as /api/tags/."
            )
            raise PaperlessMcpError(message, hint=hint)
        return RawReply(
            status=found.status,
            content_type=found.headers.get("Content-Type", ""),
            body=found.content,
            truncated=found.truncated,
        )
