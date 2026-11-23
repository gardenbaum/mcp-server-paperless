"""The text the toolset guide adds to every session stays small (PLMCP-82, AC 4).

The project has no tokenizer; its limits on text sent to the model count UTF-8 bytes
(``TEXT_LIMIT`` in test_registry.py), so these do too (English text is about four bytes a token).
The limits are tight on purpose: a longer guide has to raise them here, with a reason, instead of
growing unnoticed. They are measured over the combinations that make the guide longest: the
smallest selections, read-only mode (a cut-down toolset names its tools), a capability that could
not be checked and a disabled overview tool. The default tool budget counts tools only.
"""

import itertools
import json
from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeUser, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.server import INSTRUCTIONS

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-41")]

OVERVIEW = "get_instance_overview"
# What the instructions and the overview tool cost before the guide, measured on main (731b462).
BASE_INSTRUCTIONS = 331
BASE_OVERVIEW_DEFINITION = 4442
# The limits, in bytes: the instructions as a whole (so the guide adds at most the difference to
# BASE_INSTRUCTIONS), the guide in a call of the overview, and the growth of the overview's
# definition in tools/list (its output schema).
INSTRUCTIONS_LIMIT = 1400  # measured 1392 at most; the default profile sends 1190
OVERVIEW_GUIDE_LIMIT = 2750  # measured 2742 at most; the default profile sends 2610
DEFINITION_ADDED_LIMIT = 650  # measured 637
# Anything near a limit is a reason to look; a limit this far above the worst case is not tight.
SLACK = 0.9

SELECTIONS = (["core"], ["core", "ai"], None, ["core", "ai", "trash"], ["raw"], ["core", "raw"])


def size(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def reader(*, ai_known: bool) -> FakePaperless:
    state = seeded()
    state.ai_enabled = True
    # Without view_uisettings the AI flag is unknown, so the ai toolset is listed with that gap.
    state.users.append(FakeUser(id=9, username="no-settings", permissions=frozenset()))
    return FakePaperless(state, user="admin" if ai_known else "no-settings")


async def measured(
    toolsets: list[str] | None, *, read_only: bool, ai_known: bool, overview: bool
) -> tuple[int, int]:
    """Bytes of the instructions and of the overview's guide (0 without the overview tool)."""
    server = serve(
        reader(ai_known=ai_known),
        toolsets=toolsets,
        read_only=read_only,
        disabled_tools=() if overview else (OVERVIEW,),
    )
    async with Client(server) as client:
        guide = 0
        if OVERVIEW in {tool.name for tool in await client.list_tools()}:
            result = await client.call_tool(OVERVIEW)
            guide = size(cast("dict[str, object]", result.structured_content)["toolsets"])
        return len((client.instructions or "").encode()), guide


async def test_the_instructions_and_the_overview_stay_within_their_limits() -> None:
    worst_instructions = worst_guide = 0
    for toolsets, read_only, ai_known, overview in itertools.product(
        SELECTIONS, (False, True), (True, False), (True, False)
    ):
        instructions, guide = await measured(
            toolsets, read_only=read_only, ai_known=ai_known, overview=overview
        )
        worst_instructions = max(worst_instructions, instructions)
        worst_guide = max(worst_guide, guide)

    assert worst_instructions <= INSTRUCTIONS_LIMIT
    assert worst_guide <= OVERVIEW_GUIDE_LIMIT
    assert worst_instructions >= INSTRUCTIONS_LIMIT * SLACK
    assert worst_guide >= OVERVIEW_GUIDE_LIMIT * SLACK


async def test_the_guide_is_added_to_the_instructions_that_were_there() -> None:
    instructions, _ = await measured(["core"], read_only=False, ai_known=True, overview=True)
    async with Client(serve(reader(ai_known=True))) as client:
        text = client.instructions or ""

    assert text.startswith(INSTRUCTIONS)
    assert len(INSTRUCTIONS.encode()) == BASE_INSTRUCTIONS
    assert instructions > BASE_INSTRUCTIONS


async def test_the_overview_definition_stays_within_its_limit() -> None:
    async with Client(serve(reader(ai_known=True))) as client:
        [tool] = [t for t in await client.list_tools() if t.name == OVERVIEW]
    definition = size(tool.model_dump(mode="json", by_alias=True, exclude_none=True))

    assert definition - BASE_OVERVIEW_DEFINITION <= DEFINITION_ADDED_LIMIT
    assert definition - BASE_OVERVIEW_DEFINITION >= DEFINITION_ADDED_LIMIT * SLACK
