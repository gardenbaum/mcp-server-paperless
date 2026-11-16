"""Prompts (design 5.6, decision-20): guided tasks that name tools, propose before they write.

`prompt_problems` is the check behind "a prompt names only tools that exist and belong to its
declared toolsets"; it reads the real tool registry (TOOL_TOOLSETS) and the declared toolsets.
The self-tests run it over a synthetic provider of violating prompts, so it is proven to fire.
"""

from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client, FastMCP
from fastmcp.server.providers import LocalProvider
from harness import admin, serve

from paperless_mcp.server import CATALOGUE
from paperless_mcp.tools._shared import ANNOTATIONS, TOOL_TOOLSETS, TOOLSETS
from paperless_mcp.tools.prompts import CONFIRMATION, SPECS, PromptSpec, mentioned_tools

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mcp.types import TextContent

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-20")]

PROMPTS = {"triage_inbox", "find_document", "annual_review", "cleanup_taxonomy"}
# Arguments that render each shipped prompt.
SAMPLES: dict[str, dict[str, str]] = {
    "triage_inbox": {},
    "find_document": {"description": "the invoice from the plumber last spring"},
    "annual_review": {"year": "2025"},
    "cleanup_taxonomy": {},
}


async def render(server: FastMCP, name: str, arguments: dict[str, str] | None = None) -> str:
    async with Client(server) as client:
        result = await client.get_prompt(name, arguments or {})
    return "\n".join(cast("TextContent", message.content).text for message in result.messages)


def prompt_problems(
    text: str,
    declared: frozenset[str],
    registry: Mapping[str, str] = TOOL_TOOLSETS,
) -> list[str]:
    """The tools a prompt text names that do not exist, or are not in its declared toolsets."""
    return [
        f"{name} does not exist"
        if name not in registry
        else f"{name} is in toolset {registry[name]}, not in {sorted(declared)}"
        for name in sorted(mentioned_tools(text))
        if registry.get(name) not in declared
    ]


def full_server() -> FastMCP:
    return serve(admin(ai_enabled=True), toolsets=["all"])


async def prompt_names(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {prompt.name for prompt in await client.list_prompts()}


# --- The four prompts --------------------------------------------------------------------------


async def test_the_four_prompts_exist_with_a_description() -> None:
    async with Client(full_server()) as client:
        prompts = {prompt.name: prompt for prompt in await client.list_prompts()}

    assert set(prompts) == PROMPTS
    assert all((prompt.description or "").strip() for prompt in prompts.values())


async def test_every_prompt_declares_toolsets_that_exist_and_is_tagged_with_one_of_them() -> None:
    prompts = {prompt.name: prompt for prompt in await full_server().list_prompts()}

    assert set(SPECS) == PROMPTS
    for name, spec in SPECS.items():
        assert spec.toolsets, name
        assert spec.toolsets <= TOOLSETS, name
        assert len(prompts[name].tags) == 1, name
        assert prompts[name].tags <= spec.toolsets, name


@pytest.mark.parametrize("name", sorted(PROMPTS))
async def test_a_prompt_proposes_changes_and_waits_for_confirmation_before_writing(
    name: str,
) -> None:
    text = await render(full_server(), name, SAMPLES[name])

    assert CONFIRMATION in text
    assert "propose" in CONFIRMATION.lower()
    assert "wait" in CONFIRMATION.lower()
    assert "confirm" in CONFIRMATION.lower()


@pytest.mark.parametrize("name", sorted(PROMPTS))
async def test_a_prompt_names_only_tools_that_exist_in_its_declared_toolsets(name: str) -> None:
    text = await render(full_server(), name, SAMPLES[name])

    assert prompt_problems(text, SPECS[name].toolsets) == []


@pytest.mark.parametrize("name", sorted(PROMPTS))
async def test_a_prompt_declares_exactly_the_tools_and_toolsets_it_uses(name: str) -> None:
    text = await render(full_server(), name, SAMPLES[name])
    spec = SPECS[name]

    assert mentioned_tools(text) == spec.tools
    assert {TOOL_TOOLSETS[tool] for tool in spec.tools} == spec.toolsets


async def test_the_prompts_that_write_name_writing_tools_and_find_document_only_reads() -> None:
    writing = {name for name, hints in ANNOTATIONS.items() if not hints.read_only_hint}

    assert SPECS["find_document"].tools.isdisjoint(writing)
    for name in ("triage_inbox", "annual_review", "cleanup_taxonomy"):
        assert not SPECS[name].tools.isdisjoint(writing), name


async def test_a_prompt_that_deletes_tells_the_model_to_use_the_confirm_flow() -> None:
    text = await render(full_server(), "cleanup_taxonomy")

    assert "confirm=true" in text


async def test_the_arguments_reach_the_text() -> None:
    server = full_server()

    assert "plumber" in await render(server, "find_document", SAMPLES["find_document"])
    assert "2025" in await render(server, "annual_review", SAMPLES["annual_review"])


# --- Self-tests: the check fires on prompts that break the rule --------------------------------

offenders = LocalProvider()


@offenders.prompt(tags={"core"})
def names_a_ghost() -> str:
    """Names a tool that does not exist."""
    return "First call `ghost_tool`, then `search_documents`."


@offenders.prompt(tags={"core"})
def names_a_tool_of_another_toolset() -> str:
    """Names a tool of the bulk toolset while declaring core."""
    return "Call `search_documents`, then `bulk_edit_documents`."


@offenders.prompt(tags={"core"})
def names_only_declared_tools() -> str:
    """A correct prompt."""
    return "Call `search_documents` with page_size=5 and read it with `get_document`."


@offenders.prompt(tags={"core"})
def never_waits() -> str:
    """Writes without the confirmation rule."""
    return "Call `update_document` for every inbox document."


async def synthetic(name: str) -> str:
    return await render(FastMCP(providers=[offenders]), name)


async def test_self_test_a_tool_that_does_not_exist_is_found() -> None:
    text = await synthetic("names_a_ghost")

    assert prompt_problems(text, frozenset({"core"})) == ["ghost_tool does not exist"]


async def test_self_test_a_tool_outside_the_declared_toolsets_is_found() -> None:
    text = await synthetic("names_a_tool_of_another_toolset")

    assert prompt_problems(text, frozenset({"core"})) == [
        "bulk_edit_documents is in toolset bulk, not in ['core']"
    ]
    assert prompt_problems(text, frozenset({"core", "bulk"})) == []


async def test_self_test_a_correct_prompt_is_not_found() -> None:
    assert prompt_problems(await synthetic("names_only_declared_tools"), frozenset({"core"})) == []


async def test_self_test_the_tools_named_are_read_from_backticked_identifiers_only() -> None:
    text = await synthetic("names_only_declared_tools")

    assert mentioned_tools(text) == {"search_documents", "get_document"}


async def test_self_test_a_prompt_without_the_confirmation_rule_is_found() -> None:
    assert CONFIRMATION not in await synthetic("never_waits")


async def test_the_check_reads_the_real_registry() -> None:
    assert set(TOOL_TOOLSETS) >= {tool for spec in SPECS.values() for tool in spec.tools}
    assert prompt_problems("`get_document`", frozenset({"core"})) == []
    assert prompt_problems("`get_document`", frozenset({"bulk"})) == [
        "get_document is in toolset core, not in ['bulk']"
    ]


# --- Visibility: a prompt is offered only while every tool it names can be called --------------


async def test_the_default_profile_offers_the_prompts() -> None:
    assert await prompt_names(serve(admin(ai_enabled=True))) == PROMPTS


async def test_a_prompt_is_hidden_when_its_toolset_is_not_selected() -> None:
    assert await prompt_names(serve(admin(), toolsets=["bulk"])) == set()


async def test_read_only_mode_keeps_the_prompt_that_only_reads() -> None:
    server = serve(admin(), toolsets=["all"], read_only=True)

    assert await prompt_names(server) == {"find_document"}


async def test_disabling_a_tool_a_prompt_names_hides_that_prompt_only() -> None:
    server = serve(admin(), disabled_tools=["delete_taxonomy_item"])

    assert await prompt_names(server) == PROMPTS - {"cleanup_taxonomy"}


async def test_a_prompt_naming_a_tool_of_a_missing_capability_is_hidden() -> None:
    needs_ai = replace(
        CATALOGUE,
        prompt_specs={
            **CATALOGUE.prompt_specs,
            "triage_inbox": PromptSpec(
                toolsets=frozenset({"core", "ai"}), tools=frozenset({"get_ai_suggestions"})
            ),
        },
    )

    without = serve(admin(ai_enabled=False), catalogue=needs_ai, toolsets=["core", "ai"])
    with_ai = serve(admin(ai_enabled=True), catalogue=needs_ai, toolsets=["core", "ai"])

    assert "triage_inbox" not in await prompt_names(without)
    assert "triage_inbox" in await prompt_names(with_ai)


async def test_the_prompts_follow_the_capability_of_each_start() -> None:
    state = seeded()
    state.ai_enabled = True
    needs_ai = replace(
        CATALOGUE,
        prompt_specs={
            **CATALOGUE.prompt_specs,
            "triage_inbox": PromptSpec(
                toolsets=frozenset({"core", "ai"}), tools=frozenset({"get_ai_suggestions"})
            ),
        },
    )
    server = serve(FakePaperless(state, user="admin"), catalogue=needs_ai, toolsets=["core", "ai"])

    first = "triage_inbox" in await prompt_names(server)
    state.ai_enabled = False
    second = "triage_inbox" in await prompt_names(server)

    assert (first, second) == (True, False)
