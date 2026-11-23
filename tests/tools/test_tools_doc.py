"""docs/tools.md is the tool surface as a page for readers, generated so it cannot drift (PLMCP-51).

`just docs-tools` (this test with `--write-docs`) writes the page from the surface the server
offers with every toolset on. Without the option the test fails when the page differs.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import admin, serve

from mcp_server_paperless.settings import ServerSettings
from mcp_server_paperless.tools._shared import TOOL_TOOLSETS, TOOLSETS

if TYPE_CHECKING:
    from mcp.types import Prompt, Tool

pytestmark = pytest.mark.anyio

PAGE = Path(__file__).resolve().parents[2] / "docs" / "tools.md"
# The defaults of the server settings, without reading the environment.
DEFAULT_TOOLSETS = ServerSettings.model_construct().toolsets
# The end of a sentence: a full stop before a capital letter (not the one in "e.g. failed").
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

INTRO = """\
# Tool reference

<!-- Generated from the tool surface by `just docs-tools`. Do not edit by hand. -->

{tools} tools in {toolsets} toolsets, and {prompts} prompts. The toolsets {default} are offered by
default; the others are switched on with
[`PAPERLESS_MCP_TOOLSETS`](configuration.md#toolsets), which also says what each toolset is for.

**Kind** is what a tool tells the client about itself (its MCP annotations): `read` changes
nothing, `write` adds or changes data, `destructive` can remove or overwrite data. **confirm**
marks a tool with a [confirmation step](configuration.md#confirmation) for its risky changes:
without `confirm=true` such a call asks you in a dialog, or changes nothing and answers with what
it would do. **dry run** marks a tool that can preview its effect first.
"""


def summary(description: str) -> str:
    """The first sentence of a tool description, on one line and safe in a table cell."""
    first = " ".join(description.strip().split("\n\n")[0].split())
    return SENTENCE_END.split(first)[0].replace("|", "\\|")


def kind(tool: Tool) -> str:
    """read, write or destructive, with the confirmation and preview steps the tool has."""
    hints = tool.annotations
    assert hints is not None, f"{tool.name} has no annotations"
    if hints.read_only_hint:
        words = ["read"]
    elif hints.destructive_hint:
        words = ["destructive"]
    else:
        words = ["write"]
    parameters = cast("dict[str, object]", tool.input_schema.get("properties", {}))
    words += [
        label
        for name, label in (("confirm", "confirm"), ("dry_run", "dry run"))
        if name in parameters
    ]
    return ", ".join(words)


def ordered_toolsets() -> list[str]:
    """The default toolsets first, then the others by name."""
    return [*DEFAULT_TOOLSETS, *sorted(TOOLSETS - set(DEFAULT_TOOLSETS))]


def render(tools: list[Tool], prompts: list[Prompt]) -> str:
    by_name = {tool.name: tool for tool in tools}
    default = " and ".join(f"`{name}`" for name in DEFAULT_TOOLSETS)
    parts = [
        INTRO.format(
            tools=len(tools), toolsets=len(TOOLSETS), prompts=len(prompts), default=default
        )
    ]
    for toolset in ordered_toolsets():
        names = [name for name, owner in TOOL_TOOLSETS.items() if owner == toolset]
        suffix = " (on by default)" if toolset in DEFAULT_TOOLSETS else ""
        rows = [f"## `{toolset}`{suffix}", "", "| Tool | Kind | What it does |", "|---|---|---|"]
        rows += [
            f"| `{name}` | {kind(by_name[name])} | {summary(by_name[name].description or '')} |"
            for name in names
        ]
        parts.append("\n".join(rows) + "\n")
    rows = [
        "## Prompts",
        "",
        "Guided multi-step tasks the client offers (in Claude Code as slash commands). Each one",
        "proposes its changes and waits for a yes before it writes.",
        "",
        "| Prompt | What it does |",
        "|---|---|",
    ]
    rows += [
        f"| `{prompt.name}` | {summary(prompt.description or '')} |"
        for prompt in sorted(prompts, key=lambda prompt: prompt.name)
    ]
    parts.append("\n".join(rows) + "\n")
    return "\n".join(parts)


async def surface() -> tuple[list[Tool], list[Prompt]]:
    # AI on, so that capability gating cannot hide the ai toolset from the page.
    async with Client(serve(admin(ai_enabled=True), toolsets=["all"])) as client:
        return await client.list_tools(), await client.list_prompts()


def test_a_summary_is_the_first_sentence_on_one_line() -> None:
    text = "Delete a view for good: it has\nno trash. No document is touched.\n\nMore."

    assert summary(text) == "Delete a view for good: it has no trash."
    assert summary("Mark as seen, e.g. failed ones.") == "Mark as seen, e.g. failed ones."
    assert summary("Read a | b.") == "Read a \\| b."


async def test_the_page_covers_the_whole_catalogue() -> None:
    tools, _ = await surface()

    assert {tool.name for tool in tools} == set(TOOL_TOOLSETS)


async def test_every_destructive_tool_has_a_confirmation_step() -> None:
    """The README says so; decision-6 and the registry tests are where the rule lives."""
    tools, _ = await surface()

    assert [tool.name for tool in tools if kind(tool) == "destructive"] == []


async def test_the_tool_reference_matches_the_surface(request: pytest.FixtureRequest) -> None:
    page = render(*await surface())
    if request.config.getoption("--write-docs"):
        _ = PAGE.write_text(page, encoding="utf-8")

    assert PAGE.exists(), "docs/tools.md is missing: run `just docs-tools`"
    assert PAGE.read_text(encoding="utf-8") == page, "docs/tools.md is stale: run `just docs-tools`"
