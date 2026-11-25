"""What the server tells the assistant about the toolsets that are off (PLMCP-82).

Every combination of the selected toolsets, read-only mode, disabled tools and the capability
Paperless reports is started, and the guide in the server instructions and in the overview is
checked against what the server really does: switching on exactly the advertised toolsets, and
nothing else, adds exactly the advertised tools. Nothing is advertised that read-only mode, a
disabled tool or a missing capability excludes.
"""

import itertools
from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakePaperless, FakeUser, seeded
from fastmcp import Client
from guide_oracle import (
    CANONICAL,
    COMBINATIONS,
    DISABLED,
    OVERVIEW,
    SELECTIONS,
    Seen,
    check,
    everything,
    look,
    observe,
    refused,
    resolved,
    started,
    verify,
)
from harness import (
    FailingReader,
    admin,
    paperless_settings,
    refused_url,
    serve,
    server_settings,
)

from mcp_server_paperless.domain.errors import InvalidConfiguration, UpstreamUnavailable
from mcp_server_paperless.server import CATALOGUE, create_server
from mcp_server_paperless.tools._shared import TOOL_TOOLSETS, TOOLSETS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.instance import InstanceOverview
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


@pytest.mark.parametrize(
    ("capability", "combination"), list(itertools.product(READERS, COMBINATIONS))
)
async def test_the_guide_matches_what_switching_the_toolsets_on_would_add(
    capability: str, combination: tuple[str, str, bool]
) -> None:
    await check(READERS[capability], capability=capability, combination=combination)


async def test_the_default_profile_tells_the_assistant_what_is_off_and_how_to_get_it() -> None:
    seen = await started(present(), None)

    assert seen is not None
    assert OVERVIEW in seen.tools
    assert "Toolsets on: core, ai." in seen.instructions
    assert "trash (" in seen.instructions
    assert "PAPERLESS_MCP_TOOLSETS=core,ai,<name>" in seen.instructions
    assert "bulk (" in seen.instructions  # listed, but no example to follow
    assert "PAPERLESS_MCP_TOOLSETS=core,ai,bulk" not in seen.instructions


async def test_without_the_overview_tool_the_instructions_do_not_send_the_assistant_to_it() -> None:
    seen = await started(present(), None, disabled_tools=(OVERVIEW,))

    assert seen is not None
    assert OVERVIEW not in seen.instructions


async def test_with_everything_on_the_instructions_have_no_guide_and_no_setting() -> None:
    seen = await started(present(), ["all"])

    assert seen is not None
    assert "PAPERLESS_MCP_TOOLSETS" not in seen.instructions
    assert seen.guide is not None
    assert (seen.guide["off"], seen.guide["switch_on"]) == ([], None)


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


# A word each summary must carry, so that a summary under the wrong toolset is noticed.
KEYWORDS = {
    "core": "taxonomy",
    "ai": "AI",
    "bulk": "many",
    "versions": "versions",
    "views": "views",
    "trash": "deleted",
    "pdf": "PDF",
    "sharing": "share",
    "permissions": "permissions",
    "mail": "accounts",
    "raw": "endpoints",
    "workflows": "workflows",
    "admin": "logs",
    "tasks": "background",
}


def test_every_summary_says_what_its_own_toolset_is_for_and_no_other() -> None:
    assert set(KEYWORDS) == TOOLSETS
    for toolset, word in KEYWORDS.items():
        assert word in CATALOGUE.summaries[toolset], toolset
        others = [t for t, summary in CATALOGUE.summaries.items() if word in summary]
        assert others == [toolset], (word, others)


def offered(seen: Seen) -> list[str]:
    assert seen.guide is not None
    return [entry["toolset"] for entry in cast("list[dict[str, str]]", seen.guide["off"])]


class Says:
    """Paperless as it answers the AI question at the moment: on, off or unknown (None)."""

    def __init__(self, ai_enabled: bool | None) -> None:  # noqa: FBT001 -- on, off or unknown
        self.ai_enabled: bool | None = ai_enabled
        self._fake: FakePaperless = admin()

    async def get_overview(self) -> InstanceOverview:
        return (await self._fake.get_overview()).model_copy(update={"ai_enabled": self.ai_enabled})


async def test_the_guide_of_the_overview_follows_what_paperless_says_at_the_time_of_the_call() -> (
    None
):
    paperless = Says(ai_enabled=True)

    async with Client(serve(paperless, toolsets=["core"])) as client:
        first = await look(client)
        paperless.ai_enabled = False
        second = await look(client)
        paperless.ai_enabled = None
        third = await look(client)

    assert "ai" in offered(first)
    assert "ai" not in offered(second)  # a restart would not deliver it
    assert "ai" in offered(third)
    assert (first.guide or {})["unchecked"] == {}
    assert (third.guide or {})["unchecked"] == {"ai": "Paperless AI"}
    # What is offered stays as the start found it, whatever Paperless says later.
    assert first.tools == second.tools == third.tools


async def test_what_is_on_stays_as_the_start_found_it() -> None:
    state = seeded()
    state.ai_enabled = False
    server = serve(FakePaperless(state, user="admin"))

    async with Client(server) as client:
        state.ai_enabled = True
        seen = await look(client)

    assert (seen.guide or {})["on"] == ["core"]  # ai is selected but was hidden at the start
    assert (seen.guide or {})["offered_after_restart"] == ["ai"]
    assert "ai" not in offered(seen)


@pytest.mark.parametrize("combination", COMBINATIONS[::5])
async def test_a_paperless_that_does_not_answer_is_a_gap_with_the_real_adapter(
    combination: tuple[str, str, bool],
) -> None:
    """The real adapter against a port nothing listens on: the capability is unchecked."""
    selection, disabled, read_only = combination
    settings = server_settings(
        toolsets=SELECTIONS[selection], read_only=read_only, disabled_tools=DISABLED[disabled]
    )
    ceiling = server_settings(
        toolsets=everything(SELECTIONS[selection]),
        read_only=read_only,
        disabled_tools=DISABLED[disabled],
    )
    url = refused_url()
    try:
        current = await observe(create_server(paperless_settings(url), settings), overview=False)
    except InvalidConfiguration:
        assert refused(selection, disabled)
        return
    wide = await observe(create_server(paperless_settings(url), ceiling), overview=False)

    assert current is not None
    assert wide is not None
    verify(
        current,
        wide,
        capability="silent",
        selected=resolved(SELECTIONS[selection]),
        read_only=read_only,
    )


async def test_the_gap_of_a_toolset_that_is_on_follows_the_current_answer() -> None:
    paperless = Says(ai_enabled=True)

    async with Client(serve(paperless)) as client:
        first = await look(client)
        paperless.ai_enabled = None
        second = await look(client)

    assert (first.guide or {})["unchecked"] == {}
    assert (second.guide or {})["unchecked"] == {"ai": "Paperless AI"}
    assert (second.guide or {})["on"] == ["core", "ai"]
    assert first.tools == second.tools


async def look_started(
    reader: InstanceReader, toolsets: Sequence[str] | None, disabled: Sequence[str]
) -> Seen:
    seen = await started(reader, toolsets, disabled_tools=disabled, overview=False)
    assert seen is not None
    return seen


@pytest.mark.parametrize("disabled", [(), ("ask_paperless_ai", "get_ai_suggestions")])
@pytest.mark.parametrize("selection", ["default", "core", "core-ai-trash", "all", "mixed"])
@pytest.mark.parametrize("now", [True, False, None])
@pytest.mark.parametrize("started", [True, False, None])
async def test_the_overview_after_the_answer_changed_says_what_a_restart_would_deliver(
    *, started: bool | None, now: bool | None, selection: str, disabled: tuple[str, ...]
) -> None:
    """Started on one answer about AI, asked on another: what is on, offered and off."""
    paperless = Says(ai_enabled=started)
    toolsets = SELECTIONS[selection]
    async with Client(serve(paperless, toolsets=toolsets, disabled_tools=disabled)) as client:
        paperless.ai_enabled = now
        seen = await look(client)
    restarted = await look_started(Says(now), toolsets, disabled)
    ceiling = await look_started(Says(now), everything(toolsets), disabled)
    assert seen.guide is not None

    def toolsets_of(tools: frozenset[str]) -> list[str]:
        return [n for n in CANONICAL if any(TOOL_TOOLSETS[t] == n for t in tools)]

    on = toolsets_of(seen.tools)
    delivered = toolsets_of(restarted.tools - seen.tools)
    off = toolsets_of(ceiling.tools - restarted.tools)
    chosen = resolved(toolsets)

    def marked(names: list[str]) -> list[str]:
        return [f"{n} (read-only)" if f"{n}:read" in chosen else n for n in names]

    assert seen.guide["on"] == marked(on)
    assert seen.guide["offered_after_restart"] == marked(delivered)
    assert [e["toolset"] for e in cast("list[dict[str, str]]", seen.guide["off"])] == off
    # The gap follows the current answer, for any toolset the guide mentions.
    mentioned = {*on, *delivered, *off}
    expected = {"ai": "Paperless AI"} if now is None and "ai" in mentioned else {}
    assert seen.guide["unchecked"] == expected
