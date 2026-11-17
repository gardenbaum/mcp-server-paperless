"""The raw part of FakePaperless: a few real endpoints answered like Paperless 3.4.2 does.

The fake serves only what the contract tests and the tool tests ask for: the tag list (JSON,
paged by ``page_size``, only the tags the user may see), a document's file (``application/pdf``
or the document's own type) and nothing else; any other path is a 404 as for an unknown
endpoint. A path without its trailing slash is answered with a redirect, which the adapter
refuses like it does on the live stack (Django's APPEND_SLASH).
"""

import json
import re
from typing import TYPE_CHECKING

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.raw import RawReply

if TYPE_CHECKING:
    from collections.abc import Mapping


def redirect() -> PaperlessMcpError:
    """The error for an answer that redirects."""
    return PaperlessMcpError(
        "Paperless redirected the request; redirects are not followed.",
        hint=(
            "The path is probably wrong or lacks its trailing slash; "
            "ask for an exact path such as /api/tags/."
        ),
    )


def unknown() -> PaperlessMcpError:
    """The error for a path that is no endpoint: 3.4.2 redirects it (to its web app)."""
    return redirect()


def answer(
    path: str,
    params: Mapping[str, str],
    tags: list[tuple[int, str]],
    file: tuple[bytes, str] | None,
    *,
    max_bytes: int,
) -> RawReply:
    """The reply for ``path``; ``tags`` are (id, name) the user sees, ``file`` is (bytes, type)."""
    if path == "/api/tags" or re.fullmatch(r"/api/documents/\d+/download", path):
        raise redirect()
    if path == "/api/tags/":
        size = int(params.get("page_size", "25"))
        page = [{"id": number, "name": name} for number, name in tags][:size]
        body = json.dumps(
            {"count": len(tags), "next": None, "previous": None, "results": page}
        ).encode()
        content_type = "application/json"
    elif file is not None and re.fullmatch(r"/api/documents/\d+/download/", path):
        body, content_type = file
    else:
        raise unknown()
    if len(body) > max_bytes:
        return RawReply(status=200, content_type=content_type, body=b"", truncated=True)
    return RawReply(status=200, content_type=content_type, body=body, truncated=False)
