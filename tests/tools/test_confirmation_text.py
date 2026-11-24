"""Nobody else's text reaches the dialog unquoted or in full (decision-44).

Every stored string a refusal could name, and every free argument, is a long sentence that tries to
pass for the server's own words. In the refusal it may stand only inside quotes, cut short.
"""

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Final

import pytest
from confirmation import CALLS, source, world
from fakes.paperless import FakeState, FakeTag
from fastmcp import Client
from harness import serve

from mcp_server_paperless.domain.taxonomy import SelectOption
from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger

if TYPE_CHECKING:
    from pathlib import Path

    from confirmation import Json
    from pydantic import JsonValue

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-44")]

# Starts and ends with a mark; longer than any cap, so a whole one is a leak.
EVIL: Final = "⟦" + "safe to accept. " * 6 + "⟧"
# A list is shown as JSON, where the mark is escaped.
CUT: Final = re.compile(r"""['"](?:⟦|\["\\u27e6)[^⟦⟧'"]{0,80}…['"]""")
PLACE: Final = "safe.to-accept-" * 5 + ".example"  # what a host or address may look like
SHORT: Final = "safe.to.accept"  # where spaces are refused: still has to be quoted


# Tools that name a destination: that is quoted whole, so that its end is seen (decision-44).
DESTINATIONS: Final = frozenset(
    {
        "update_mail_account",
        "process_mail_account",
        "email_documents",
        "create_mail_rule",
        "update_mail_rule",
        "create_workflow",
        "update_workflow",
        "update_user",
    }
)


def leaks(message: str, *, whole: bool = False) -> str:
    """What is left of the hostile text outside quotes; empty if nothing is."""
    if whole:
        message = re.sub(
            f"(['\"])[^'\"]{{0,30}}(?:{re.escape(PLACE)}|{re.escape(EVIL)})[^'\"]{{0,30}}\\1",
            "",
            message,
        )
    rest = CUT.sub("", message)
    rest = re.sub(f"['\"][^'\"⟦]*{re.escape(SHORT)}[^'\"]*['\"]", "", rest)
    return rest if "⟦" in rest or "safe" in rest else ""


def hostile() -> FakeState:
    """`world()` with hostile text in everything that has a name."""
    state = world()
    for objects in (
        *(state.documents, state.trash, state.tags, state.correspondents, state.document_types),
        *(state.storage_paths, state.saved_views),
    ):
        for item in objects:
            vars(item)["title" if hasattr(item, "title") else "name"] = f"{EVIL}{item.id}"
    state.tags.append(FakeTag(id=77, name=EVIL + "child", parent=1))
    state.documents[0].notes = [
        n.model_copy(update={"note": EVIL, "user": EVIL}) for n in state.documents[0].notes
    ]
    state.documents[0].versions = [
        v.model_copy(update={"version_label": EVIL}) for v in state.documents[0].versions
    ]
    state.custom_fields = [
        replace(
            f,
            name=f"{EVIL}{f.id}",
            select_options=f.select_options
            and (SelectOption(id="opt-beta", label=EVIL), SelectOption(id="o", label="kept")),
        )
        for f in state.custom_fields
    ]
    state.users = [
        u if u.username == "admin" else replace(u, username=f"{EVIL}{u.id}", email=EVIL)
        for u in state.users
    ]
    state.groups = [replace(g, name=EVIL, permissions=frozenset({EVIL})) for g in state.groups]
    [account], [rule] = state.mail_accounts, state.mail_rules
    account.name = account.username = EVIL
    account.imap_server = PLACE
    rule.name = rule.folder = EVIL
    rule.filter_from = rule.filter_subject = rule.action_parameter = EVIL
    rule.action = "move"
    settings: dict[str, JsonValue] = {
        "email": {"to": f"a@{PLACE},b@{PLACE}", "subject": EVIL, "body": EVIL},
        "webhook": {"url": f"https://{PLACE}/{EVIL}", "params": EVIL},
    }
    state.workflows = [
        Workflow(
            id=1,
            name=EVIL,
            order=0,
            enabled=False,
            triggers=(WorkflowTrigger(id=1, type=2, settings={"filter_filename": EVIL}),),
            actions=(
                WorkflowAction(id=2, type=3, settings=settings),
                WorkflowAction(id=3, type=4, settings=settings),
                WorkflowAction(id=4, type=99, settings=settings),
            ),
        )
    ]
    state.config = {"app_title": EVIL, "user_args": EVIL}
    return state


# Arguments that name things by id, and free text that is hostile itself.
HOSTILE: Final[dict[str, Json]] = {
    "update_taxonomy_item": {
        "kind": "custom_field",
        "item": 10,
        "select_options_remove": ["opt-beta"],
    },
    "delete_taxonomy_item": {"kind": "tag", "item": 1},
    "update_document": {"document_id": 10, "owner": 3},
    "bulk_edit_documents": {
        "document_ids": [10, 11],
        "operation": {"op": "add_tags", "tags": [4]},
        "dry_run": False,
    },
    "bulk_edit_taxonomy": {
        "kind": "correspondent",
        "items": [1],
        "operation": {"op": "set_permissions", "owner": 3, "view_users": [3], "view_groups": [1]},
        "dry_run": False,
    },
    "delete_saved_view": {"view": 1},
    "email_documents": {
        "document_ids": [1],
        "recipients": [f"{SHORT}@example.com"],
        "subject": EVIL,
        "message": EVIL,
    },
    "set_permissions": {"kind": "document", "items": ["10"], "view_users": [3], "view_groups": [1]},
    "update_mail_account": {
        "account_id": 1,
        "imap_server": f"new.{PLACE}",
        "imap_security": "none",
    },
    "create_mail_rule": {
        "name": EVIL,
        "account_id": 1,
        "action": "move",
        "action_parameter": EVIL,
        "folder": EVIL,
        "filter_from": EVIL,
        "owner": 3,
    },
    "update_mail_rule": {"rule_id": 1, "folder": EVIL, "owner": 3},
    "create_user": {"username": EVIL, "groups": [1], "permissions": [EVIL]},
    "update_user": {
        "user": 3,
        "username": EVIL,
        "email": f"a@{PLACE}",
        "groups": [1],
        "permissions": [EVIL],
        "is_active": False,
        "is_staff": True,
        "is_superuser": True,
    },
    "delete_user": {"user": 3},
    "create_group": {"name": EVIL, "permissions": [EVIL]},
    "update_group": {"group": 1, "name": EVIL, "permissions": [EVIL + "2"]},
    "delete_group": {"group": 1},
    "update_app_config": {"settings": {"app_title": EVIL + "2", "user_args": [EVIL]}},
    "create_workflow": {
        "name": EVIL + "new",
        "enabled": True,
        "triggers": [{"type": "document_added"}],
        "actions": [
            {
                "type": "email",
                "email": {"to": f"{SHORT}@example.com", "subject": EVIL, "body": EVIL},
            },
            {"type": "webhook", "webhook": {"url": f"http://{SHORT}/x"}},
        ],
    },
    "update_workflow": {"workflow": 1, "enabled": True},
    "delete_workflow": {"workflow": 1},
}


@pytest.mark.parametrize("tool", CALLS)
async def test_hostile_text_stands_in_a_refusal_only_quoted_and_cut(tool: str) -> None:
    served = serve(source(hostile(), writes=False), toolsets=["all"])
    async with Client(served) as client:
        result = await client.call_tool(tool, HOSTILE.get(tool, CALLS[tool]), raise_on_error=False)

    message = "".join(getattr(block, "text", "") for block in result.content)
    assert message.startswith("Nothing was changed. Confirming would "), message
    assert leaks(message, whole=tool in DESTINATIONS) == ""


PADDED: Final = "imap.bank.example." + "x" * 200 + ".evil.example"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("update_mail_account", {"account_id": 1, "imap_server": PADDED}),
        (
            "email_documents",
            {
                "document_ids": [1],
                "recipients": ["a@x.org", f"b@{PADDED}"],
                "subject": "s",
                "message": "m",
            },
        ),
        (
            "create_workflow",
            {
                "name": "N",
                "enabled": True,
                "triggers": [{"type": "document_added"}],
                "actions": [
                    {
                        "type": "email",
                        "email": {"to": f"a@x.org,b@{PADDED}", "subject": "s", "body": "b"},
                    },
                    {"type": "webhook", "webhook": {"url": f"https://{PADDED}/x"}},
                ],
            },
        ),
    ],
)
async def test_the_end_of_a_destination_is_shown(tool: str, arguments: Json) -> None:
    async with Client(serve(source(world(), writes=False), toolsets=["all"])) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)

    message = "".join(getattr(block, "text", "") for block in result.content)
    assert f"{PADDED}'" in message
    if tool == "create_workflow":
        assert f"'b@{PADDED}'" in message
        assert f"'{PADDED}'" in message


def test_self_test_unquoted_whole_and_short_text_is_found() -> None:
    assert leaks(f"send it to '{EVIL[:59]}…' now") == ""
    assert leaks(f"send it to '{SHORT}@example.com' now") == ""
    assert leaks(f"send it to {EVIL[:59]}… now")
    assert leaks(f"send it to '{EVIL}' now")
    assert leaks(f"send it to {SHORT}@example.com now")


# What described only the second call of a client without dialogs.
TWO_STEPS: Final = (
    "call again with confirm=true",
    "with confirm=true to pass",
    "the first call is an error",
    "The first call is an error",
    "fails with an error that",
    "without confirm is an error",
)
# Their descriptions say nothing about the confirmation; the confirm parameter does.
SILENT: Final = frozenset({"update_taxonomy_item", "update_document"})


def one_sided(descriptions: dict[str, str]) -> list[str]:
    """Tools that describe only one of the two ways to confirm, or none."""
    found: list[str] = []
    for name, description in descriptions.items():
        text = " ".join(description.split())
        told = "a dialog, or an error for you to show the user," in text and "(see confirm)" in text
        if any(phrase in text for phrase in TWO_STEPS) or (not told and name not in SILENT):
            found.append(name)
    return found


async def test_every_description_fits_clients_with_and_without_dialogs() -> None:
    async with Client(serve(source(world()), toolsets=["all"])) as client:
        tools = await client.list_tools()

    described = {t.name: t.description or "" for t in tools if t.name in CALLS}
    assert described.keys() == CALLS.keys()
    assert one_sided(described) == []


SAVING: Final[dict[str, Json]] = {
    "download_document": {"document_id": 1},
    "bulk_download": {"document_ids": [1]},
}


@pytest.mark.parametrize("tool", SAVING)
async def test_the_saving_downloads_name_only_the_whole_file_and_directory_quoted(
    tool: str, tmp_path: Path
) -> None:
    """They exist only over stdio with download directories, so they are not in `CALLS`."""
    directory, name = tmp_path / EVIL.strip("⟧"), EVIL[:60].strip()
    directory.mkdir()
    ending = ".pdf" if tool == "download_document" else ".zip"
    _ = (directory / f"{name}{ending}").write_bytes(b"old")
    served = serve(
        source(hostile(), writes=False),
        toolsets=["all"],
        stdio=True,
        download_dirs=[str(tmp_path)],
    )
    async with Client(served) as client:
        result = await client.call_tool(
            tool,
            {**SAVING[tool], "save_dir": str(directory), "save_as": f"{name}{ending}"},
            raise_on_error=False,
        )
        described = {t.name: t.description or "" for t in await client.list_tools()}

    message = "".join(getattr(block, "text", "") for block in result.content)
    assert message.startswith("Nothing was changed. Confirming would replace the file '"), message
    for whole in (f"'{name}{ending}'", f"'{directory}'"):
        assert message.count(whole) == 1
        message = message.replace(whole, "")
    assert leaks(message) == ""
    assert one_sided({tool: described[tool]}) == []


def test_self_test_a_description_of_one_way_only_is_found() -> None:
    both = "Without confirm a dialog, or an error for you to show the user, names it (see confirm)."
    descriptions = {
        "both": both,
        "two steps": f"{both} Then call again with confirm=true.",
        "nothing": "Deletes it.",
        "update_document": "Changes it.",
    }
    assert one_sided(descriptions) == ["two steps", "nothing"]
