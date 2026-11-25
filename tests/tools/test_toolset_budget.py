"""The text the toolset guide adds to every session stays small (PLMCP-82, AC 4).

The project has no tokenizer; its limits on text sent to the model count UTF-8 bytes
(``TEXT_LIMIT`` in test_registry.py, 2048 for the instructions), so these do too (English text is
about four bytes a token). The limits are tight on purpose: a longer guide has to raise them here,
with a reason, instead of growing unnoticed. They are measured on the worst case, which is built
from the real catalogue and searched, not sampled:

- Instructions: every toolset but one is listed, so the selection is each single toolset in turn,
  also read-only (PLMCP-90: its mark and the suffix in the value), and from there every read-only
  toolset whose mark and suffix are longer than its line among those that are off (``grown``);
  a toolset is described by its summary, or by ``PARTIAL`` where that is longer and a disabled
  tool cuts it down; Paperless AI is unknown (its gap is named); the overview tool is on or off.
  The instructions name no tool, so the count of tools does not matter.
- Guide of the overview: its text lists the tools of every toolset that is off, so the worst case
  is ``core`` alone with nothing cut down, Paperless AI unknown or on (the overview needs ``core``).
  Over HTTP and over stdio with and without upload directories, which add ``upload_documents``
  (PLMCP-85).
- Definition: the output schema of the overview tool in tools/list.

The default tool budget counts tools only.
"""

import itertools
import json
from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakePaperless, FakeUser, seeded
from fastmcp import Client
from harness import admin, serve

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
#
# Measured 1423 at most before PLMCP-90 (core alone, AI unknown, cut down), 1440 with core:read, and
# 1451 with core, views, pdf and workflows all read-only, whose lines among those that are off are
# shorter. The owner approved 1451 on 2026-11-25 ("Alles wie empfohlen", all as recommended) and,
# once PLMCP-89 is in, what is measured then, at most 1474.
INSTRUCTIONS_LIMIT = 1451
# Measured 2943 (core alone, nothing cut, stdio with upload directories): 19 bytes of
# upload_documents (PLMCP-85) and 21 of resolve_duplicates (PLMCP-86) over the 2758 of HTTP before,
# both approved by the owner on 2026-11-23; and 37 of get_trash_entry and get_trashed_file
# (PLMCP-78), approved by the owner on 2026-11-24; and 14 of inspect_pdf
# (PLMCP-79.2), 16 of extract_pages (PLMCP-79.3) and 15 of insert_pages (PLMCP-79.4): the owner
# approved on 2026-11-24 at most 2880 for the three tools of PLMCP-79; and 17 of read_documents
# (PLMCP-80.1), 31 of update_documents and add_notes (PLMCP-80.3) and 15 of run_workflow
# (PLMCP-80.4): the 63 bytes for the four names of PLMCP-80 (2880 to 2943) the owner approved on
# 2026-11-24. And 21 of get_processed_mail (PLMCP-81.1), of the 67 for the three names of PLMCP-81
# (2943 to 3010) the owner approved on 2026-11-24; and 24 of delete_processed_mail (PLMCP-81.3). And
# 23 of update_workflow_part (PLMCP-87.2), of the 42 of PLMCP-87 (3010 to 3052) in the steps up to
# 3229 the owner approved on 2026-11-24. And 17 of the mark and the suffix of core:read (PLMCP-90).
OVERVIEW_GUIDE_LIMIT = 3028
# Measured 879: 747 before, plus 132 of too_old (PLMCP-101); the rise from 750 was approved by the
# owner on 2026-11-24.
DEFINITION_ADDED_LIMIT = 879
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


async def measure(
    toolsets: Sequence[str],
    disabled: Sequence[str],
    reader: FakePaperless | None = None,
    upload_dirs: Sequence[str] | None = None,
) -> tuple[int, int | None]:
    """Bytes of the instructions, and of the overview's guide if the overview tool is offered."""
    server = serve(
        reader or unknown_ai(),
        toolsets=toolsets,
        disabled_tools=disabled,
        stdio=upload_dirs is not None,
        upload_dirs=upload_dirs or (),
    )
    async with Client(server) as client:
        guide = None
        if OVERVIEW in {tool.name for tool in await client.list_tools()}:
            result = await client.call_tool(OVERVIEW)
            guide = size(cast("dict[str, object]", result.structured_content)["toolsets"])
        return len((client.instructions or "").encode()), guide


def others(selection: Sequence[str]) -> list[str]:
    """``cut_down`` without the selected toolsets: there it changes no text, and may leave none."""
    on = {entry.removesuffix(":read") for entry in selection}
    return [tool for tool in cut_down() if TOOL_TOOLSETS[tool] not in on]


async def grown(selection: list[str]) -> tuple[list[str], int]:
    """The selection with every read-only toolset that makes the instructions longer, their size.

    Each toolset adds its own words only, so one after the other finds the longest.
    """
    longest, _ = await measure(selection, others(selection))
    for name in TOOLSETS:
        if name not in {entry.removesuffix(":read") for entry in selection}:
            wider = [*selection, f"{name}:read"]
            size_of_wider, _ = await measure(wider, others(wider))
            if size_of_wider > longest:
                selection, longest = wider, size_of_wider
    return selection, longest


async def worst() -> tuple[int, int]:
    """The longest instructions over the selections above, and the longest guide."""
    instructions = guide = 0
    readers = (unknown_ai, lambda: admin(ai_enabled=True))
    directories = (None, (), ("/srv/scans",))  # HTTP, stdio, stdio with upload directories
    entries = [f"{name}{suffix}" for name in TOOLSETS for suffix in ("", ":read")]
    for name, cut, overview, reader, dirs in itertools.product(
        entries, (False, True), (True, False), readers, directories
    ):
        disabled = [*(others([name]) if cut else []), *([] if overview else [OVERVIEW])]
        text, listed = await measure([name], disabled, reader(), dirs)
        instructions = max(instructions, text)
        guide = max(guide, listed or 0)
    for name in TOOLSETS:
        _, text = await grown([f"{name}:read"])
        instructions = max(instructions, text)
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
