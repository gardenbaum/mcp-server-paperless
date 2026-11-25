"""Text that cannot be sent: lone surrogates, half of a character outside the basic plane.

UTF-8 cannot hold them. JSON can, as escapes: what cuts a text in an emoji sends them.
"""

import json
import re
from typing import Final, cast

# The only argument of a call whose text was refused before it could be parsed: where the text was.
REFUSED_TEXT: Final = "mcp-server-paperless/refused-text"
# Every escape of JSON; a pair of surrogates is one character, the rest of them are lone.
_ESCAPES: Final = re.compile(
    rb"\\(?:u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}"
     rb"|(?P<lone>u[dD][89a-fA-F][0-9a-fA-F]{2})|.)",
    re.DOTALL,
)

_REPLACEMENT: Final = b"\\" + b"ufffd"


def _whole(text: str) -> bool:
    try:
        _ = text.encode()
    except UnicodeEncodeError:
        return False
    return True


def refused_text(value: object, place: str = "") -> str | None:
    """Where in the arguments of a call a lone surrogate is, or None."""
    if isinstance(value, str):
        return None if _whole(value) else place
    if isinstance(value, dict):
        for key, inner in cast("dict[str, object]", value).items():
            if not _whole(key):
                return f"name of a value in {place}" if place else "name of an argument"
            if found := refused_text(inner, f"{place}.{key}" if place else key):
                return found
    if isinstance(value, list):
        for index, inner in enumerate(cast("list[object]", value)):
            if found := refused_text(inner, f"{place}.{index}"):
                return found
    return None


def parseable(line: bytes) -> bytes:
    """A JSON-RPC message without lone surrogates, which the parser of the MCP SDK does not take.

    A call of a tool with one in its arguments becomes one that only says where it was, under
    ``REFUSED_TEXT``. Elsewhere U+FFFD takes its place. Any other line stays as it is.
    """
    cleaned = _ESCAPES.sub(lambda found: _REPLACEMENT if found["lone"] else found[0], line)
    if cleaned == line:
        return line
    try:
        given = cast("dict[str, dict[str, object]]", json.loads(line))["params"]
        place = refused_text(given.get("arguments"))
        if place is None:
            return cleaned
        message = cast("dict[str, dict[str, object]]", json.loads(cleaned))
        message["params"]["arguments"] = {REFUSED_TEXT: place}
        return json.dumps(message).encode() + b"\n"
    except ValueError, KeyError, TypeError, AttributeError, RecursionError:
        return cleaned
