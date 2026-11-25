"""The passages of the reading tools' texts that name another tool (decision-111).

A passage is left out, or replaced, while the operator hides every tool it is for. The texts
themselves stay in the toolset modules and the models, whole; a passage repeats its own words, line
breaks included, and a test keeps the two equal and finds a name that has no passage.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast, override

from fastmcp.server.transforms import Transform

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from fastmcp.tools import Tool


@dataclass(frozen=True, slots=True)
class Passage:
    """Words of a description that only make sense while one of ``tools`` is offered."""

    tools: frozenset[str]
    text: str
    instead: str = ""


def _of(*tools: str, text: str, instead: str = "") -> Passage:
    return Passage(frozenset(tools), text, instead)


RESTORE, EMPTY = "restore_from_trash", "empty_trash"
CREATE, DELETE = "create_share_link", "delete_share_link"
_ACKNOWLEDGED = _of("acknowledge_tasks", text=": what acknowledge_tasks takes")

# Per reading tool, for its description and its schemas; the passage of several tools first.
PASSAGES: Final[Mapping[str, tuple[Passage, ...]]] = {
    "run_saved_view": (_of("update_saved_view", text="; fix the\nview with update_saved_view"),),
    "get_saved_view": (
        _of("set_permissions", text=" Change it with set_permissions (kind saved_view)."),
    ),
    "list_trash": (
        _of(
            RESTORE,
            EMPTY,
            text="get_trash_entry, get_trashed_file, restore_from_trash or\nempty_trash",
            instead="get_trash_entry or get_trashed_file",
        ),
        _of(
            RESTORE,
            EMPTY,
            text="Pass it to restore_from_trash or empty_trash (a version's id).",
            instead="Of the entry (a version's id).",
        ),
        _of(RESTORE, text="get_trashed_file, restore_from_trash or", instead="get_trashed_file or"),
        _of(
            EMPTY,
            text="get_trashed_file, restore_from_trash or\nempty_trash",
            instead="get_trashed_file or restore_from_trash",
        ),
        _of(RESTORE, text="restore_from_trash or empty_trash", instead="empty_trash"),
        _of(EMPTY, text="restore_from_trash or empty_trash", instead="restore_from_trash"),
    ),
    "get_trash_entry": (_of(RESTORE, text=" (restore_from_trash, no confirm)"),),
    "get_trashed_file": (_of(RESTORE, text=" (restore_from_trash, no\nconfirm)"),),
    "list_share_links": (
        _of(
            CREATE,
            DELETE,
            text=" Create one with\ncreate_share_link, remove one with delete_share_link.",
        ),
        _of(CREATE, text=" Create one with\ncreate_share_link, remove", instead=" Remove"),
        _of(DELETE, text=", remove one with delete_share_link"),
    ),
    "list_share_bundles": (
        _of("rebuild_share_bundle", text=", and\nrebuild_share_bundle tries again"),
        _of("rebuild_share_bundle", text=" and call rebuild_share_bundle"),
    ),
    "get_permissions": (_of("set_permissions", text="For what set_permissions changes. "),),
    "list_users": (_of("set_permissions", text=" to pass to set_permissions"),),
    "list_groups": (_of("set_permissions", text=" to pass to set_permissions"),),
    "get_app_config": (_of("update_app_config", text=" Change it with update_app_config."),),
    "list_tasks": (
        _of(
            "acknowledge_tasks",
            text="the database id acknowledge_tasks takes",
            instead="the database id",
        ),
        _ACKNOWLEDGED,
    ),
    "get_task_overview": (_ACKNOWLEDGED,),
    "list_workflows": (_of("create_workflow", text=";\nthey use the names create_workflow takes"),),
    "explain_workflow": (_of("create_workflow", text=", named as create_workflow takes it"),),
}


def _without(value: object, passages: Sequence[Passage]) -> object:
    """A schema whose descriptions lack these passages; nothing else of it changes."""
    if isinstance(value, dict):
        return {
            key: _shorter(inner, passages)
            if key == "description" and isinstance(inner, str)
            else _without(inner, passages)
            for key, inner in cast("dict[str, object]", value).items()
        }
    if isinstance(value, list):
        return [_without(inner, passages) for inner in cast("list[object]", value)]
    return value


def _shorter(text: str, passages: Sequence[Passage]) -> str:
    for passage in passages:
        text = text.replace(passage.text, passage.instead)
    return text


class HiddenNames(Transform):
    """Leaves out of the listed tools the passages whose tools are all hidden."""

    def __init__(
        self, hidden: Collection[str], passages: Mapping[str, tuple[Passage, ...]] = PASSAGES
    ) -> None:
        """``hidden``: what read-only mode, a read-only toolset or a disabled tool takes away.

        Tests pass ``passages`` of their own.
        """
        self._passages: dict[str, list[Passage]] = {
            name: gone
            for name, marked in passages.items()
            if (gone := [passage for passage in marked if passage.tools <= set(hidden)])
        }

    @override
    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._listed(tool) for tool in tools]

    def _listed(self, tool: Tool) -> Tool:
        passages = self._passages.get(tool.name)
        if not passages:
            return tool
        return tool.model_copy(
            update={
                "description": _shorter(tool.description or "", passages),
                "parameters": _without(tool.parameters, passages),
                "output_schema": _without(tool.output_schema, passages),
            }
        )
