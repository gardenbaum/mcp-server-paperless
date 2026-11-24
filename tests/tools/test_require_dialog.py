"""PAPERLESS_MCP_REQUIRE_DIALOG: only an accepted dialog confirms, confirm=true does not.

Off (the default) is what `test_confirmation.py` tests. Declining cases use a fake that refuses
writes, so a write fails the test.
"""

import ast
import copy
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import anyio
import pytest
from confirmation import CALLS, NOT_AGAIN, NOTE, YES, answering, source, world
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from harness import serve

from mcp_server_paperless.domain.errors import CONFIRM_HINT
from mcp_server_paperless.tools._shared import boundary
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
NO_FORMS = (
    "the user cannot be asked on this connection, or the client does not declare form elicitation "
    "(MCP dialogs)"
)
WAY_OUT = "a client that shows dialogs can confirm, or the operator can switch that off"
# Tools whose confirm exists only over stdio with download directories.
SAVING = ("bulk_download", "download_document")
TOOLS = sorted([*CALLS, *SAVING])


def refusal(reason: str) -> str:
    way_out = f"; {WAY_OUT}" if reason == NO_FORMS else ""
    return f"Nothing was changed: {reason}; {REQUIRED}{way_out}. Hint: {NOT_AGAIN}"


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


def own_consents(source: str) -> list[int]:
    """The lines where code does anything with a `confirm` but pass the caller's on.

    The name may be a keyword-only parameter or a field, False by default; a `confirm=` argument
    may only be that name; and it is neither assigned nor spelled as a string.
    """
    found: set[int] = set()
    declared: set[ast.expr] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign) and not no_default(node.value):
            declared.add(node.target)
        if isinstance(node, ast.arguments):
            found |= {a.lineno for a in [*node.posonlyargs, *node.args] if a.arg == "confirm"}
            found |= {
                a.lineno
                for a, default in zip(node.kwonlyargs, node.kw_defaults, strict=True)
                if a.arg == "confirm" and no_default(default)
            }
        if isinstance(node, ast.keyword) and node.arg == "confirm":
            passed = isinstance(node.value, ast.Name) and node.value.id == "confirm"
            found |= set() if passed else {node.value.lineno}
        if isinstance(node, ast.Constant) and node.value == "confirm":
            found.add(node.lineno)
        name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        if (
            name == "confirm"
            and isinstance(node, ast.Name | ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and node not in declared
        ):
            found.add(node.lineno)
    return sorted(found)


def no_default(default: ast.expr | None) -> bool:
    """Whether a default is anything but none at all or False."""
    return default is not None and not (
        isinstance(default, ast.Constant) and default.value is False
    )


def test_no_tool_or_service_confirms_for_the_caller() -> None:
    package = Path(__file__).parents[2] / "src" / "mcp_server_paperless"
    paths = sorted(package.rglob("*.py"))

    found = {
        str(path.relative_to(package)): own_consents(path.read_text(encoding="utf-8"))
        for path in paths
    }

    assert len(found) == len(paths) > 100
    boundary = found.pop(str(Path("tools", "confirmation.py")))
    assert len(boundary) == 1  # the name of the parameter it takes away
    assert {path: lines for path, lines in found.items() if lines} == {}


@pytest.mark.parametrize(
    "line",
    [
        "run(confirm=True)",
        "run(confirm=not dry_run)",
        "run(confirm=edit.confirm)",
        "dict(confirm=True)",
        'run(**{"confirm": True})',
        'kwargs["confirm"] = True',
        'put(edit, "confirm", True)',
        "confirm = True",
        "edit.confirm = True",
        "run((confirm := True))",
        "def run(confirm): ...",
        "def run(confirm, /): ...",
        "def run(*, confirm=True): ...",
        "confirm: bool = True",
    ],
)
def test_self_test_code_that_confirms_for_the_caller_is_found(line: str) -> None:
    assert own_consents(line) == [1]


@pytest.mark.parametrize(
    "line",
    [
        "run(confirm=confirm)",
        "def run(*, confirm: bool = False): ...",
        "def run(*, confirm): ...",
        "confirm: bool = False",
        "if not edit.confirm: pass",
    ],
)
def test_self_test_code_that_passes_the_callers_confirm_on_is_not(line: str) -> None:
    assert own_consents(line) == []


# The HTTP variants of the download tools have no confirm (test_downloads.py): nothing is saved.
@both_eras
@pytest.mark.parametrize("tool", ["upload_document", "upload_document_version"])
async def test_the_http_variants_of_the_upload_tools_need_the_dialog(mode: Mode, tool: str) -> None:
    arguments: Json = {**CALLS[tool], "confirm": True}

    no = await call(server(source(world(), writes=False)), mode, None, tool, arguments)
    state, seen = world(), list[str]()
    before = copy.deepcopy(state)
    yes = await call(
        server(source(state)), mode, answering("accept", YES, seen=seen), tool, arguments
    )

    assert text(no) == refusal(NO_FORMS)
    assert not yes.is_error, text(yes)
    assert len(seen) == 1
    assert state != before


async def test_a_parameter_named_like_the_boundarys_own_reaches_the_tool() -> None:
    async def run(*, tool: str) -> str:
        return tool

    assert await boundary(run)(tool="x") == "x"


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


@both_eras
@pytest.mark.parametrize("save_as", [None, "x.pdf"])
@pytest.mark.parametrize("tool", SAVING)
async def test_confirm_without_a_directory_is_ignored_and_a_name_still_refused(
    tmp_path: Path, mode: Mode, tool: str, save_as: str | None
) -> None:
    """Deliberate: with the setting off, confirm alone is refused here; it only reads."""
    which: Json = {"document_id": 13} if tool == "download_document" else {"document_ids": [13]}
    arguments = {**which, "confirm": True} | ({"save_as": save_as} if save_as else {})

    result = await call(
        server(source(world(), writes=False), tmp_path), mode, None, tool, arguments
    )

    assert files(tmp_path) == {}
    if save_as:
        assert result.is_error
        assert "save_as and confirm are for saving a file." in text(result)
    else:
        assert not result.is_error, text(result)
        assert [b.type for b in result.content] == ["text", "resource"]


async def test_it_is_off_by_default() -> None:
    state = world()

    result = await call(
        serve(source(state), toolsets=["all"]), "auto", None, "delete_note", CONFIRMED
    )

    assert not result.is_error
    assert state.documents[0].notes == []
