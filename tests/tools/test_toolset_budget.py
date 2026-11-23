"""The text the toolset guide adds to every session stays small (PLMCP-82, AC 4).

The project has no tokenizer; its limits on text sent to the model count UTF-8 bytes
(``TEXT_LIMIT`` in test_registry.py, 2048 for the instructions), so these do too (English text is
about four bytes a token). The limits are tight on purpose: a longer guide has to raise them here,
with a reason, instead of growing unnoticed. They are measured on the worst case, which is built
from the real catalogue and searched, not sampled:

- Instructions: every toolset but one is listed, so the selection is each single toolset in turn;
  a toolset is described by its summary, or by ``PARTIAL`` where that is longer and a disabled
  tool cuts it down; Paperless AI is unknown (its gap is named); the overview tool is on or off.
  The instructions name no tool, so the count of tools does not matter.
- Guide of the overview: its text lists the tools of every toolset that is off, so the worst case
  is ``core`` alone with nothing cut down and Paperless AI unknown (the overview needs ``core``).
- Definition: the output schema of the overview tool in tools/list.

The default tool budget counts tools only.
"""

import itertools
import json
from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakePaperless, FakeUser, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.server import CATALOGUE
from mcp_server_paperless.tools._shared import TOOL_TOOLSETS
from mcp_server_paperless.tools.selection import PARTIAL

if TYPE_CHECKING:
    from collections.abc import Sequence

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-41")]

OVERVIEW = "get_instance_overview"
# What the instructions and the overview tool cost before the guide, measured on main (731b462).
BASE_INSTRUCTIONS = 331
BASE_OVERVIEW_DEFINITION = 4442
# The limits, in bytes: the instructions as a whole (so the guide adds at most the difference to
# BASE_INSTRUCTIONS), the guide in a call of the overview, and the growth of the overview's
# definition in tools/list (its output schema).
INSTRUCTIONS_LIMIT = 1450  # measured 1431 at most (core alone, a cut-down toolset each)
OVERVIEW_GUIDE_LIMIT = 2750  # measured 2712 at most (core alone, nothing cut down)
DEFINITION_ADDED_LIMIT = 660  # measured 650
# The limit of every instructions text (test_registry.py TEXT_LIMIT): Claude Code cuts at 4,096.
TEXT_LIMIT = 2048
# Anything near a limit is a reason to look; a limit this far above the worst case is not tight.
SLACK = 0.9

TOOLSETS = list(dict.fromkeys(TOOL_TOOLSETS.values()))


def size(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def unknown_ai() -> FakePaperless:
    """A reader that may not view the UI settings: the AI flag is unknown."""
    state = seeded()
    state.users.append(FakeUser(id=9, username="no-settings", permissions=frozenset()))
    return FakePaperless(state, user="no-settings")


def cut_down() -> list[str]:
    """One tool of every toolset that ``PARTIAL`` describes in more bytes than its summary."""
    return [
        next(tool for tool, toolset in TOOL_TOOLSETS.items() if toolset == name)
        for name in TOOLSETS
        if len(PARTIAL) > len(CATALOGUE.summaries[name])
        and sum(1 for toolset in TOOL_TOOLSETS.values() if toolset == name) > 1
    ]


async def measure(toolsets: Sequence[str], disabled: Sequence[str]) -> tuple[int, int | None]:
    """Bytes of the instructions, and of the overview's guide if the overview tool is offered."""
    async with Client(serve(unknown_ai(), toolsets=toolsets, disabled_tools=disabled)) as client:
        guide = None
        if OVERVIEW in {tool.name for tool in await client.list_tools()}:
            result = await client.call_tool(OVERVIEW)
            guide = size(cast("dict[str, object]", result.structured_content)["toolsets"])
        return len((client.instructions or "").encode()), guide


async def worst() -> tuple[int, int]:
    """The longest instructions over every single-toolset selection, and the longest guide."""
    instructions = guide = 0
    for name, cut, overview in itertools.product(TOOLSETS, (False, True), (True, False)):
        disabled = [*(cut_down() if cut else []), *([] if overview else [OVERVIEW])]
        text, listed = await measure([name], disabled)
        instructions = max(instructions, text)
        guide = max(guide, listed or 0)
    return instructions, guide


async def test_the_worst_case_lists_every_toolset_but_one_and_cuts_down_those_it_can() -> None:
    assert cut_down()
    async with Client(serve(unknown_ai(), toolsets=["raw"], disabled_tools=cut_down())) as client:
        text = client.instructions or ""

    assert all(f"{name} (" in text for name in TOOLSETS if name != "raw")
    assert PARTIAL in text
    assert "not checked" in text


async def test_the_instructions_and_the_overview_stay_within_their_limits() -> None:
    instructions, guide = await worst()

    assert instructions <= INSTRUCTIONS_LIMIT < TEXT_LIMIT
    assert guide <= OVERVIEW_GUIDE_LIMIT
    assert instructions >= INSTRUCTIONS_LIMIT * SLACK
    assert guide >= OVERVIEW_GUIDE_LIMIT * SLACK


async def test_the_overview_definition_stays_within_its_limit() -> None:
    async with Client(serve(unknown_ai())) as client:
        [tool] = [t for t in await client.list_tools() if t.name == OVERVIEW]
    definition = size(tool.model_dump(mode="json", by_alias=True, exclude_none=True))

    assert definition - BASE_OVERVIEW_DEFINITION <= DEFINITION_ADDED_LIMIT
    assert definition - BASE_OVERVIEW_DEFINITION >= DEFINITION_ADDED_LIMIT * SLACK
