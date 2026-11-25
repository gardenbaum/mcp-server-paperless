"""Text that cannot be sent: lone surrogates, half of a character outside the basic plane.

UTF-8 cannot hold them. JSON can, as escapes: what cuts a text in an emoji sends them.
"""

import json
import re
from typing import Final, cast

from pydantic import TypeAdapter

# The only argument of a call whose text was refused before it could be parsed: where the text was.
REFUSED_TEXT: Final = "mcp-server-paperless/refused-text"
# The method of any other request that was: none has this name, so the answer repeats it.
REFUSED_METHOD: Final = "refused: the message holds half of a character (a lone surrogate)"
MAX_PLACE: Final = 100
# Nearly every line has none of these and is not looked at any further.
_SURROGATE: Final = re.compile(rb"\\u[dD][89a-fA-F]")
# Every escape of JSON; a pair of surrogates is one character, the rest of them are lone.
_HEX: Final = "[0-9a-fA-F]{2}"
_ESCAPES: Final = re.compile(
    rf"\\(?:u[dD][89abAB]{_HEX}\\u[dD][c-fC-F]{_HEX}|(?P<lone>u[dD][89a-fA-F]{_HEX})|.)", re.DOTALL
)
_REPLACEMENT: Final = "\\" + "ufffd"
# The parser of the MCP SDK: what it does not take for another reason must not be taken here.
_JSON: Final = TypeAdapter[object](object)

type _Object = dict[str, object]


def _whole(text: str) -> bool:
    try:
        _ = text.encode()
    except UnicodeEncodeError:
        return False
    return True


def _place(value: object, place: str) -> str | None:
    if isinstance(value, str):
        return None if _whole(value) else place or "arguments"
    if isinstance(value, dict):
        for key, inner in cast("_Object", value).items():
            if not _whole(key):
                return f"name of a value in {place}" if place else "name of an argument"
            if found := _place(inner, f"{place}.{key}" if place else key):
                return found
    if isinstance(value, list):
        for index, inner in enumerate(cast("list[object]", value)):
            if found := _place(inner, f"{place or 'arguments'}.{index}"):
                return found
    return None


def refused_text(value: object, place: str = "") -> str | None:
    """Where in the arguments of a call a lone surrogate is, or None."""
    found = _place(value, place)
    return found if found is None or len(found) <= MAX_PLACE else found[: MAX_PLACE - 1] + "…"


def parseable(line: bytes) -> bytes:
    """A line of the stdio transport, or a request to refuse if a lone surrogate is why it has none.

    The parser of the MCP SDK does not take such a request, which then has no answer. It becomes one
    that cannot do anything: a call of a tool that only says where the text was, under
    ``REFUSED_TEXT``, or the method ``REFUSED_METHOD``. Any other line stays as it is, also one that
    the parser does not take for another reason.
    """
    if _SURROGATE.search(line) is None:
        return line
    text = line.decode("utf-8", "replace")  # as the SDK reads it
    cleaned = _ESCAPES.sub(lambda found: _REPLACEMENT if found["lone"] else found[0], text)
    # The SDK ends a line at a carriage return, too.
    if cleaned == text or "\r" in text.removesuffix("\n").removesuffix("\r"):
        return line
    try:
        message, given = _JSON.validate_json(cleaned), cast("object", json.loads(text))
    except ValueError:
        return line
    if not isinstance(message, dict) or not isinstance(given, dict):
        return line
    return _refusal(cast("_Object", message), cast("_Object", given)) or line


def _refusal(message: _Object, given: _Object) -> bytes | None:
    """``message`` as a request to refuse; None if it is no request that can be answered."""
    number, method = message.get("id"), message.get("method")
    params, original = message.get("params", {}), given.get("params", {})
    if type(number) not in {int, str} or number != given.get("id") or not isinstance(method, str):
        return None
    if not isinstance(params, dict) or not isinstance(original, dict):
        return None
    params, original = cast("_Object", params), cast("_Object", original)
    if method == "tools/call":
        place = refused_text(original.get("arguments"))
        for name, value in original.items():
            place = place or (refused_text(value, name) if _whole(name) else None)
        changed = {"params": {**params, "arguments": {REFUSED_TEXT: place or "message"}}}
    else:
        meta = {"_meta": params["_meta"]} if "_meta" in params else {}
        changed = {"method": REFUSED_METHOD, "params": meta}
    return json.dumps({**message, **changed}).encode() + b"\n"
