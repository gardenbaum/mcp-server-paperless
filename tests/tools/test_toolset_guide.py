"""What the server tells the assistant about the toolsets that are off (PLMCP-82).

Every combination of the selected toolsets, read-only mode, disabled tools and the capability
Paperless reports is started, and the guide in the server instructions and in the overview is
checked against what the server really does: switching on exactly the advertised toolsets, and
nothing else, adds exactly the advertised tools. Nothing is advertised that read-only mode, a
disabled tool or a missing capability excludes.
"""

import itertools
from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakePaperless, FakeUser, seeded
from fastmcp import Client
from guide_oracle import OVERVIEW, Seen, observe, verify
from harness import FailingReader, admin, serve

from mcp_server_paperless.domain.errors import InvalidConfiguration, UpstreamUnavailable
from mcp_server_paperless.server import CATALOGUE
from mcp_server_paperless.tools._shared import TOOLSETS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.ports import InstanceReader

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-41")]


def present() -> FakePaperless:
    return admin(ai_enabled=True)


def missing() -> FakePaperless:
    return admin(ai_enabled=False)


def unknown() -> FakePaperless:
    state = seeded()
    state.ai_enabled = False
    state.users.append(FakeUser(id=9, username="no-settings", permissions=frozenset()))
    return FakePaperless(state, user="no-settings")  # the AI flag reads as null


def silent() -> FailingReader:
    return FailingReader(UpstreamUnavailable("Paperless does not answer."))


READERS = {"present": present, "missing": missing, "unknown": unknown, "silent": silent}
SELECTIONS: dict[str, list[str] | None] = {
    "default": None,
    "core": ["core"],
    "core-ai-trash": ["core", "ai", "trash"],
    "all": ["all"],
    "trash": ["trash"],
    "raw": ["raw"],
}
DISABLED = {
    "none": (),
    "part-of-trash": ("restore_from_trash", "empty_trash"),
    "all-of-trash": ("list_trash", "restore_from_trash", "empty_trash"),
    "the-overview": (OVERVIEW,),
}


async def started(
    reader: InstanceReader,
    toolsets: Sequence[str] | None,
    *,
    read_only: bool = False,
    disabled_tools: Sequence[str] = (),
) -> Seen | None:
    """What a client sees of the server, or None if the settings leave nothing to offer."""
    try:
        server = serve(
            reader, toolsets=toolsets, read_only=read_only, disabled_tools=disabled_tools
        )
    except InvalidConfiguration:
        return None
    return await observe(server, overview=not isinstance(reader, FailingReader))


@pytest.mark.parametrize(
    ("capability", "selection", "disabled", "read_only"),
    list(itertools.product(READERS, SELECTIONS, DISABLED, (False, True))),
)
async def test_the_guide_matches_what_switching_the_toolsets_on_would_add(
    capability: str, selection: str, disabled: str, *, read_only: bool
) -> None:
    kept = DISABLED[disabled]
    current = await started(
        READERS[capability](), SELECTIONS[selection], read_only=read_only, disabled_tools=kept
    )
    ceiling = await started(
        READERS[capability](), ["all"], read_only=read_only, disabled_tools=kept
    )
    if current is None:
        return  # nothing is offered, so there is nothing to tell
    assert ceiling is not None
    verify(current, ceiling, capability=capability)


async def test_the_default_profile_tells_the_assistant_what_is_off_and_how_to_get_it() -> None:
    seen = await started(present(), None)

    assert seen is not None
    assert OVERVIEW in seen.tools
    assert "Toolsets on: core, ai." in seen.instructions
    assert "trash (" in seen.instructions
    assert "PAPERLESS_MCP_TOOLSETS=core,ai,bulk" in seen.instructions


async def test_without_the_overview_tool_the_instructions_do_not_send_the_assistant_to_it() -> None:
    seen = await started(present(), None, disabled_tools=(OVERVIEW,))

    assert seen is not None
    assert OVERVIEW not in seen.instructions


async def test_with_everything_on_the_instructions_have_no_guide_and_no_setting() -> None:
    seen = await started(present(), ["all"])

    assert seen is not None
    assert "PAPERLESS_MCP_TOOLSETS" not in seen.instructions
    assert seen.guide is not None
    assert (seen.guide["off"], seen.guide["enable"]) == ([], None)


async def test_one_server_describes_the_capability_of_each_start() -> None:
    state = seeded()
    state.ai_enabled = False
    server = serve(FakePaperless(state, user="admin"), toolsets=["core"])

    async with Client(server) as first:
        before = first.instructions or ""
    state.ai_enabled = True
    async with Client(server) as second:
        after = second.instructions or ""

    assert "ai (" not in before
    assert "ai (" in after


def test_every_toolset_has_a_summary_and_only_toolsets_do() -> None:
    assert set(CATALOGUE.summaries) == TOOLSETS
