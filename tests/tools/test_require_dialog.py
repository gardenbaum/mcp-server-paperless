"""PAPERLESS_MCP_REQUIRE_DIALOG: only an accepted dialog confirms, confirm=true does not.

Off (the default) is what `test_confirmation.py` tests. Declining cases use a fake that refuses
writes, so a write fails the test.
"""

import copy
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import anyio
import pytest
from confirmation import CALLS, NOT_AGAIN, NOTE, YES, answering, source, world
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from harness import serve

from mcp_server_paperless.domain.errors import CONFIRM_HINT
from mcp_server_paperless.tools.confirmation import MAX_DIALOGS, Dialogs

if TYPE_CHECKING:
    from confirmation import Handler, Json
    from fakes.paperless import FakePaperless
    from fastmcp.client.client import CallToolResult

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-53")]

type Mode = Literal["auto", "legacy"]
both_eras = pytest.mark.parametrize("mode", ["auto", "legacy"])

SETTING = "PAPERLESS_MCP_REQUIRE_DIALOG"
REQUIRED = (
    f"the operator requires the user's answer in a dialog ({SETTING}=true) and confirm=true does "
    "not replace it"
)
NO_FORMS = "this client does not say that it can show dialogs (MCP form elicitation)"
# Tools whose confirm exists only over stdio with download directories.
SAVING = ("bulk_download", "download_document")
TOOLS = sorted([*CALLS, *SAVING])


def refusal(reason: str) -> str:
    return f"Nothing was changed: {reason}; {REQUIRED}. Hint: {NOT_AGAIN}"


def server(
    fake: FakePaperless,
    root: Path | None = None,
    dialogs: Dialogs | None = None,
    *,
    required: bool = True,
) -> FastMCP:
    return serve(
        fake,
        toolsets=["all"],
        dialogs=dialogs,
        require_dialog=required,
        stdio=root is not None,
        upload_dirs=[str(root)] if root else (),
        download_dirs=[str(root)] if root else (),
    )


async def call(
    served: FastMCP, mode: Mode, handler: Handler | None, tool: str, arguments: Json
) -> CallToolResult:
    async with Client(served, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


def text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def arguments_of(tool: str, root: Path) -> Json:
    """Arguments of `tool` that need the confirmation; saving tools find their file there."""
    if tool not in SAVING:
        return CALLS[tool]
    if tool == "download_document":
        _ = (root / "x.pdf").write_bytes(b"old")
        return {"document_id": 13, "save_as": "x.pdf", "save_dir": str(root)}
    _ = (root / "documents.zip").write_bytes(b"old")
    return {"document_ids": [13], "content": "originals", "save_dir": str(root)}


def files(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in root.iterdir()}


def confirmations(schema: object, path: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Where in a schema a property has a name with `confirm` in it."""
    found: set[tuple[str, ...]] = set()
    if isinstance(schema, list):
        for item in cast("list[object]", schema):
            found |= confirmations(item, path)
    if isinstance(schema, dict):
        for key, value in cast("dict[str, object]", schema).items():
            if key == "properties" and isinstance(value, dict):
                names = cast("dict[str, object]", value)
                found |= {(*path, n) for n in names if "confirm" in n.lower()}
                for name, of in names.items():
                    found |= confirmations(of, (*path, name))
            else:
                found |= confirmations(value, (*path, key))
    return found


# --- every tool -----------------------------------------------------------------------------------


async def test_every_tool_with_a_confirm_parameter_is_tested_here(tmp_path: Path) -> None:
    """Over both transports. A tool that takes its confirmation elsewhere fails here, too."""
    offered = [
        *await server(source(world())).list_tools(),
        *await server(source(world()), tmp_path).list_tools(),
    ]

    found = {t.name: confirmations(t.parameters) for t in offered}
    assert sorted(name for name, where in found.items() if where) == TOOLS
    assert {where for paths in found.values() for where in paths} == {("confirm",)}


def test_self_test_a_confirmation_elsewhere_in_a_schema_is_found() -> None:
    nested: Json = {
        "properties": {"edit": {"anyOf": [{"properties": {"Confirmed": {}}}]}},
        "$defs": {"X": {"properties": {"confirm": {}}}},
    }

    assert confirmations(nested) == {("edit", "anyOf", "Confirmed"), ("$defs", "X", "confirm")}


def test_no_tool_or_service_confirms_for_the_caller() -> None:
    sources = Path(__file__).parents[2] / "src" / "mcp_server_paperless"

    texts = {p.name: p.read_text(encoding="utf-8") for p in sources.rglob("*.py")}

    assert [name for name, code in texts.items() if re.search(r"confirm=True\b", code)] == []


@both_eras
@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("tool", TOOLS)
async def test_a_client_without_dialogs_changes_nothing(
    tmp_path: Path, mode: Mode, tool: str, *, confirm: bool
) -> None:
    arguments = {**arguments_of(tool, tmp_path), "confirm": confirm}
    kept = files(tmp_path)

    result = await call(
        server(source(world(), writes=False), tmp_path), mode, None, tool, arguments
    )

    assert result.is_error
    assert text(result) == refusal(NO_FORMS)
    assert files(tmp_path) == kept


@both_eras
@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("tool", TOOLS)
async def test_only_an_accepted_dialog_lets_a_tool_run(
    tmp_path: Path, mode: Mode, tool: str, *, confirm: bool
) -> None:
    plain = arguments_of(tool, tmp_path)
    arguments = {**plain, "confirm": confirm}
    kept = files(tmp_path)
    off = server(source(world(), writes=False), tmp_path, required=False)
    shown = text(await call(off, mode, None, tool, plain)).removesuffix(f" Hint: {CONFIRM_HINT}")
    declined: list[str] = []
    no = await call(
        server(source(world(), writes=False), tmp_path),
        mode,
        answering("decline", seen=declined),
        tool,
        arguments,
    )
    assert files(tmp_path) == kept
    state = world()
    before = copy.deepcopy(state)
    accepted: list[str] = []
    yes = await call(
        server(source(state), tmp_path),
        mode,
        answering("accept", YES, seen=accepted),
        tool,
        arguments,
    )

    assert declined == accepted == [shown]
    assert text(no) == refusal("the user declined")
    assert not yes.is_error, text(yes)
    assert (files(tmp_path) != kept) if tool in SAVING else (state != before)


# --- the ways a dialog ends -----------------------------------------------------------------------

CONFIRMED: Json = {**NOTE, "confirm": True}


@both_eras
@pytest.mark.parametrize(
    ("action", "content", "reason"),
    [
        ("decline", None, "the user declined"),
        ("cancel", None, "the user closed the dialog without answering"),
        ("accept", {"confirm": False}, "the user declined"),
    ],
)
async def test_an_answer_that_is_no_acceptance_names_the_setting(
    mode: Mode, action: str, content: object, reason: str
) -> None:
    result = await call(
        server(source(world(), writes=False)),
        mode,
        answering(action, content),
        "delete_note",
        CONFIRMED,
    )

    assert text(result) == refusal(reason)


async def test_a_dialog_nobody_answers_changes_nothing() -> None:
    result = await call(
        server(source(world(), writes=False), dialogs=Dialogs(timeout=0.1)),
        "legacy",
        answering("accept", YES, before=anyio.sleep_forever),
        "delete_note",
        CONFIRMED,
    )

    assert text(result) == refusal("nobody answered the dialog in time")


async def test_a_target_that_keeps_changing_is_not_acted_on() -> None:
    state, seen = world(), list[str]()

    async def rename() -> None:
        state.documents[0].title = f"changed {len(seen)}"

    result = await call(
        server(source(state, writes=False)),
        "legacy",
        answering("accept", YES, seen=seen, before=rename),
        "delete_note",
        CONFIRMED,
    )

    assert len(seen) == MAX_DIALOGS
    assert text(result) == refusal("what would be changed kept changing while the user was asked")


async def test_a_call_without_a_client_changes_nothing() -> None:
    served = server(source(world(), writes=False))
    async with Client(served):
        with pytest.raises(ToolError) as raised:
            _ = await served.call_tool("delete_note", CONFIRMED)

    assert str(raised.value) == refusal(NO_FORMS)
    assert raised.value.log_level == logging.INFO


# --- what it leaves alone -------------------------------------------------------------------------


@both_eras
async def test_what_needs_no_confirmation_runs_without_a_dialog(mode: Mode) -> None:
    """confirm=true where nothing is to confirm is still no error."""
    state = world()

    result = await call(
        server(source(state)),
        mode,
        None,
        "update_document",
        {"document_id": 10, "title": "New", "confirm": True},
    )

    assert not result.is_error, text(result)
    assert "New" in {d.title for d in state.documents}


async def test_it_is_off_by_default() -> None:
    state = world()

    result = await call(
        serve(source(state), toolsets=["all"]), "auto", None, "delete_note", CONFIRMED
    )

    assert not result.is_error
    assert state.documents[0].notes == []
