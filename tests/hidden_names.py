"""Which tools the texts of a tool name, as a client gets them in tools/list (PLMCP-90.3)."""

import re
from typing import TYPE_CHECKING, Protocol, cast

from mcp_server_paperless.tools._shared import ANNOTATIONS, LOCAL_TOOLSETS, TOOL_TOOLSETS

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

EVERY = {*TOOL_TOOLSETS, *LOCAL_TOOLSETS}
READING = {name for name, hints in ANNOTATIONS.items() if hints.read_only_hint}
# Values, not words: `empty_trash` is a kind of task besides the tool of that name.
VALUES = {"enum", "const", "default", "examples"}


class Listed(Protocol):
    """A tool as listed over the wire."""

    @property
    def name(self) -> str: ...

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


def strings(value: object) -> Iterator[str]:
    """Every text in a JSON value, the keys included, the values a parameter can take not."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in cast("dict[str, object]", value).items():
            yield key
            if key not in VALUES:
                yield from strings(inner)
    elif isinstance(value, list):
        for inner in cast("list[object]", value):
            yield from strings(inner)


def named(tool: Listed, names: Iterable[str]) -> set[str]:
    """The other tools among ``names`` that any text of ``tool`` names."""
    text = "\n".join(strings(tool.model_dump(mode="json")))
    return {name for name in names if name != tool.name and re.search(rf"\b{name}\b", text)}
