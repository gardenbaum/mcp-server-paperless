"""Building blocks for tests of the pick dialog: a world of doubles, answers, and calls.

`CASES` holds, per write tool and place in its arguments that takes a name, a call that names the
doubles there. `ids` gives the same call with the id of one of them.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, cast

from confirmation import YES, world
from fakes.paperless import (
    FakeCustomField,
    FakeDocument,
    FakeGroup,
    FakeSavedView,
    FakeStoragePath,
    FakeTag,
    FakeTaxonomy,
    FakeUser,
)
from fastmcp.client.elicitation import ElicitResult

from mcp_server_paperless.domain.taxonomy import SelectOption
from mcp_server_paperless.domain.views import FilterRule
from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from confirmation import Handler, Json
    from fakes.paperless import FakeState

# What every test asks for, the names it matches and their ids; FIRST sorts first.
TWIN: Final = "TWIN"
NAMES: Final = ("Twin", "twin")
FIRST: Final = 50
SECOND: Final = 51
# The label of two options of the field "Choice", and the ids of these options.
OPTION: Final = "Open"
OPTIONS: Final = ("opt-one", "opt-two")
NOT_PICKED: Final = (
    "The user was asked which one is meant and picked none: do not pick one yourself, ask them."
)


def doubled() -> FakeState:
    """`world()` with two objects of every kind that "TWIN" matches."""
    state = world()
    for number, name in zip((FIRST, SECOND), NAMES, strict=True):
        state.tags.append(FakeTag(id=number, name=name))
        state.correspondents.append(FakeTaxonomy(id=number, name=name))
        state.document_types.append(FakeTaxonomy(id=number, name=name))
        state.storage_paths.append(FakeStoragePath(id=number, name=name))
        state.custom_fields.append(FakeCustomField(id=number, name=name, data_type="date"))
        state.users.append(FakeUser(id=number, username=name, mfa=True))
        state.groups.append(FakeGroup(id=number, name=name))
        state.saved_views.append(
            FakeSavedView(
                id=number,
                name=name,
                owner=2,
                filter_rules=(FilterRule(rule_type=6, value=str(number)),),
            )
        )
        state.workflows.append(
            Workflow(
                id=number,
                name=name,
                order=number,
                enabled=True,
                triggers=(
                    WorkflowTrigger(id=number, type=5, settings={"filter_has_tags": [number]}),
                ),
                actions=(WorkflowAction(id=number, type=1, settings={"assign_tags": [4]}),),
            )
        )
        state.trash.append(
            FakeDocument(id=100 + number, title=f"old of {name}", document_type=number)
        )
        state.documents.append(
            FakeDocument(
                id=number,
                title=f"of {name}",
                owner=number,
                tags=[number],
                correspondent=number,
                document_type=number,
                storage_path=number,
                custom_fields={number: "2026-01-01"},
                page_count=2,
            )
        )
    state.custom_fields.append(
        FakeCustomField(
            id=70,
            name="Choice",
            data_type="select",
            select_options=(
                SelectOption(id="opt-one", label=OPTION),
                SelectOption(id="opt-two", label=OPTION),
                SelectOption(id="opt-three", label="Paid"),
            ),
        )
    )
    state.workflows.append(
        Workflow(
            id=60,
            name="Plain",
            order=60,
            enabled=False,
            triggers=(WorkflowTrigger(id=60, type=2, settings={}),),
            actions=(WorkflowAction(id=60, type=1, settings={"assign_tags": [4]}),),
        )
    )
    return state


@dataclass
class Dialogs:
    """What a client was shown, and how it answers picks and confirmations."""

    picks: list[str | None]
    confirm: bool = True
    questions: list[str] = field(default_factory=list[str], init=False)
    confirmations: list[str] = field(default_factory=list[str], init=False)
    schemas: list[Json] = field(default_factory=list[dict[str, object]], init=False)

    @property
    def handler(self) -> Handler:
        async def answer(
            message: str, _type: object, params: object, _context: object
        ) -> ElicitResult[object]:
            schema = cast("Json", params.requested_schema)  # pyright: ignore[reportAttributeAccessIssue] -- the SDK's params
            if "choice" not in cast("Json", schema["properties"]):
                self.confirmations.append(message)
                if not self.confirm:
                    return ElicitResult[object](action="decline")
                return ElicitResult[object](action="accept", content=YES)
            self.questions.append(message)
            self.schemas.append(schema)
            pick = self.picks[min(len(self.questions), len(self.picks)) - 1]
            if pick is None:
                return ElicitResult[object](action="decline")
            return ElicitResult[object](action="accept", content={"choice": pick})

        return answer

    def before(self, act: Callable[[], Awaitable[None]]) -> Handler:
        """The same client; `act` runs while a dialog is open, once it was counted."""

        async def answer(
            message: str, kind: object, params: object, context: object
        ) -> ElicitResult[object]:
            reply = await self.handler(message, kind, params, context)
            await act()
            return reply

        return answer


def picks(*values: int | str | None, confirm: bool = True) -> Dialogs:
    """A client that answers the pick dialogs in turn (the last for all that follow)."""
    return Dialogs([None if v is None else str(v) for v in values], confirm)


def _filled(value: object, word: str, name: object) -> object:
    if isinstance(value, dict):
        entries = cast("dict[str, object]", value).items()
        return {str(name) if k == word else k: _filled(v, word, name) for k, v in entries}
    if isinstance(value, list):
        return [_filled(v, word, name) for v in cast("list[object]", value)]
    return name if value == word else value


def by_id(arguments: Json, number: int | str = SECOND, word: str = TWIN) -> Json:
    """The same call with an id instead of the name."""
    return cast("Json", _filled(arguments, word, number))


ONE: Final = ("correspondent", "document_type", "storage_path", "owner")
MANY: Final = ("tags_all", "tags_any", "tags_none")
HAS: Final = ("correspondent", "document_type", "storage_path")
HAVE: Final = ("correspondents", "document_types", "storage_paths")


def _chosen_documents(more: Json) -> Iterator[tuple[str, Json]]:
    """The places of a call that chooses its documents."""
    for name in ONE:
        yield f".filters.{name}", {"filters": {name: TWIN}, **more}
    for name in MANY:
        yield f".filters.{name}", {"filters": {name: [TWIN]}, **more}
    yield ".saved_view", {"saved_view": TWIN, **more}


GRANTS: Final = tuple(f"{may}_{who}" for may in ("view", "change") for who in ("users", "groups"))


def _grants() -> Iterator[tuple[str, Json]]:
    yield "owner", {"owner": TWIN}
    for name in GRANTS:
        yield name, {name: [TWIN]}


def _triggers() -> Iterator[tuple[str, Json]]:
    for name in ("has_tags", "has_all_tags", "has_not_tags"):
        yield name, {"type": "added", name: [TWIN]}
    for kind in HAVE:
        yield f"has_any_{kind}", {"type": "added", f"has_any_{kind}": [TWIN]}
        yield f"has_not_{kind}", {"type": "added", f"has_not_{kind}": [TWIN]}
    for kind in HAS:
        yield f"has_{kind}", {"type": "added", f"has_{kind}": TWIN}
    yield (
        "schedule_date_custom_field",
        {
            "type": "scheduled",
            "schedule_date_field": "custom_field",
            "schedule_date_custom_field": TWIN,
            "schedule_offset_days": 1,
        },
    )


def _actions() -> Iterator[tuple[str, Json]]:
    for name in (*HAS, "owner"):
        yield f"assign_{name}", {"type": "assignment", f"assign_{name}": TWIN}
    for name in ("tags", "custom_fields", *GRANTS):
        yield f"assign_{name}", {"type": "assignment", f"assign_{name}": [TWIN]}
    yield (
        "assign_custom_fields_values",
        {
            "type": "assignment",
            "assign_custom_fields_values": {TWIN: "2026-01-01"},
        },
    )
    for name in ("tags", *HAVE, "custom_fields", "owners", *GRANTS):
        yield f"remove_{name}", {"type": "removal", f"remove_{name}": [TWIN]}


TRIGGER: Final[Json] = {"type": "added"}
ACTION: Final[Json] = {"type": "assignment", "assign_title": "x"}
NEW_RULE: Final[Json] = {"name": "New", "account_id": 1, "action": "mark_read"}
META: Final = ("correspondent", "document_type", "storage_path")


def _documents() -> Iterator[tuple[str, str, Json]]:
    document: Json = {"document_id": 10}
    for name in ONE:
        yield "update_document", f".{name}", {**document, name: TWIN}
    for name in ("tags_add", "tags_set"):
        yield "update_document", f".{name}", {**document, name: [TWIN]}
    for name in ("tags_remove", "remove_custom_fields"):
        yield "update_document", f".{name}", {"document_id": SECOND, name: [TWIN]}
    for name in ONE:
        yield "update_documents", f".documents.{name}", {"documents": [{**document, name: TWIN}]}
    for name in ("tags_add", "tags_set", "tags_remove", "remove_custom_fields"):
        number = SECOND if name.endswith(("remove", "fields")) else 10
        item: Json = {"document_id": number, name: [TWIN]}
        yield "update_documents", f".documents.{name}", {"documents": [item]}
    item = {**document, "custom_fields": {TWIN: "2026-01-01"}}
    yield "update_documents", ".documents.custom_fields", {"documents": [item]}
    yield "update_document", ".custom_fields", {**document, "custom_fields": {TWIN: "2026-01-01"}}

    upload: Json = {"content_base64": "JVBERi0xLjQgbmV3", "filename": "x.pdf"}
    for name in META:
        yield "upload_document", f".{name}", {**upload, name: TWIN}
    yield "upload_document", ".tags", {**upload, "tags": [TWIN]}
    yield "upload_document", ".custom_fields", {**upload, "custom_fields": {TWIN: "2026-01-01"}}


def _bulk() -> Iterator[tuple[str, str, Json]]:
    add: Json = {"op": "add_tags", "tags": ["e2e-inbox"]}
    for tool, more in (
        ("add_notes", {"note": "Paid"}),
        ("bulk_edit_documents", {"operation": add}),
        ("run_workflow", {"workflow": "By hand", "apply_filters": False}),
    ):
        for place, arguments in _chosen_documents(cast("Json", more)):
            yield tool, place, arguments
    run: Json = {"workflow": TWIN, "document_ids": [10], "apply_filters": False}
    yield "run_workflow", ".workflow", run
    bulk: Json = {"document_ids": [10, SECOND]}
    for place, operation in (
        ("tags", {"op": "add_tags", "tags": [TWIN]}),
        ("tags", {"op": "remove_tags", "tags": [TWIN]}),
        ("add", {"op": "modify_tags", "add": [TWIN]}),
        ("remove", {"op": "modify_tags", "remove": [TWIN]}),
        ("correspondent", {"op": "set_correspondent", "correspondent": TWIN}),
        ("document_type", {"op": "set_document_type", "document_type": TWIN}),
        ("storage_path", {"op": "set_storage_path", "storage_path": TWIN}),
        ("values", {"op": "modify_custom_fields", "values": {TWIN: "2026-01-01"}}),
        ("remove", {"op": "modify_custom_fields", "remove": [TWIN]}),
    ):
        yield "bulk_edit_documents", f".operation.{place}", {**bulk, "operation": operation}


def _objects() -> Iterator[tuple[str, str, Json]]:
    remove: Json = {"op": "delete"}
    for kind in ("tag", "correspondent", "document_type", "storage_path"):
        yield (
            "bulk_edit_taxonomy",
            ".items",
            {"kind": kind, "items": [TWIN], "operation": remove},
        )
    grant: Json = {"op": "set_permissions"}
    base: Json = {"kind": "tag", "items": ["e2e-inbox"]}
    for name, grants in _grants():
        yield "bulk_edit_taxonomy", f".operation.{name}", {**base, "operation": {**grant, **grants}}
    for kind in ("tag", "correspondent", "document_type", "storage_path", "saved_view"):
        yield (
            "set_permissions",
            ".items",
            {"kind": kind, "items": [TWIN], "view_users": ["e2e-reader"]},
        )
    for name, grants in _grants():
        yield (
            "set_permissions",
            f".{name}",
            {"kind": "document", "items": [10], **grants},
        )


def _rules() -> Iterator[tuple[str, str, Json]]:
    for tool, rule in (("create_mail_rule", NEW_RULE), ("update_mail_rule", {"rule_id": 1})):
        for name in ("assign_document_type", "owner"):
            yield tool, f".{name}", {**rule, name: TWIN}
        yield tool, ".assign_tags", {**rule, "assign_tags": [TWIN]}
        yield (
            tool,
            ".assign_correspondent",
            {
                **rule,
                "assign_correspondent_from": "selected",
                "assign_correspondent": TWIN,
            },
        )

    for number in (3, 4, 6, 25, 32, 38):  # one of each kind a rule can name
        rules = [{"rule": number, "value": TWIN}]
        yield "create_saved_view", ".rules.value", {"name": "New", "rules": rules}
        yield "update_saved_view", ".rules.value", {"view": 1, "rules": rules}
    yield "update_saved_view", ".view", {"view": TWIN, "name": "New"}
    yield "delete_saved_view", ".view", {"view": TWIN}
    yield "pin_saved_view", ".view", {"view": TWIN, "dashboard": True}


def _names() -> Iterator[tuple[str, str, Json]]:
    yield "create_taxonomy_item", ".parent", {"kind": "tag", "name": "New", "parent": TWIN}
    for kind in ("tag", "correspondent", "document_type", "storage_path", "custom_field"):
        item: Json = {"kind": kind, "item": TWIN}
        yield "update_taxonomy_item", ".item", {**item, "name": "New"}
        yield "delete_taxonomy_item", ".item", item
    yield (
        "update_taxonomy_item",
        ".parent",
        {"kind": "tag", "item": "e2e-inbox", "parent": TWIN},
    )

    yield "create_user", ".groups", {"username": "new", "groups": [TWIN]}
    yield "update_user", ".groups", {"user": "e2e-reader", "groups": [TWIN]}
    yield "update_user", ".user", {"user": TWIN, "first_name": "New"}
    yield "update_user", ".user", {"user": TWIN, "remove_second_factor": True}
    yield "delete_user", ".user", {"user": TWIN}
    yield "update_group", ".group", {"group": TWIN, "name": "New"}
    yield "delete_group", ".group", {"group": TWIN}


def _workflows() -> Iterator[tuple[str, str, Json]]:
    for name, trigger in _triggers():
        with_it: Json = {"triggers": [trigger], "actions": [ACTION]}
        new: Json = {"name": "New", **with_it}
        yield "create_workflow", f".triggers.{name}", new
        yield "update_workflow", f".triggers.{name}", {"workflow": "Plain", **with_it}
        one: Json = {"workflow": "Plain", "operation": "add", "trigger": trigger}
        yield "update_workflow_part", f".trigger.{name}", one
    for name, action in _actions():
        with_it = {"triggers": [TRIGGER], "actions": [action]}
        yield "create_workflow", f".actions.{name}", {"name": "New", **with_it}
        yield "update_workflow", f".actions.{name}", {"workflow": "Plain", **with_it}
        one = {"workflow": "Plain", "operation": "add", "action": action}
        yield "update_workflow_part", f".action.{name}", one
    yield "update_workflow", ".workflow", {"workflow": TWIN, "name": "New"}
    yield "delete_workflow", ".workflow", {"workflow": TWIN}
    yield (
        "update_workflow_part",
        ".workflow",
        {"workflow": TWIN, "operation": "add", "action": ACTION},
    )


def _pages() -> Iterator[tuple[str, str, Json]]:
    for name in ("document_type", "correspondent", "tags"):
        metadata = {name: [TWIN] if name == "tags" else TWIN}
        yield (
            "split_document",
            f".part_metadata.{name}",
            {"document_id": 10, "parts": ["1", "2"], "part_metadata": [metadata, {}]},
        )
        yield (
            "extract_pages",
            f".metadata.{name}",
            {"document_id": 10, "pages": "1", "metadata": metadata},
        )
    for tool in ("empty_trash", "restore_from_trash"):
        yield tool, ".filter.document_type", {"filter": {"document_type": TWIN}}


CASES: Final[tuple[tuple[str, str, Json], ...]] = (
    *_documents(),
    *_bulk(),
    *_objects(),
    *_rules(),
    *_names(),
    *_workflows(),
    *_pages(),
)


def _options() -> Iterator[tuple[str, Json]]:
    """Calls that name an option by a label two of them have."""
    value: Json = {"custom_fields": {"Choice": OPTION}}
    yield "update_document", {"document_id": 10, **value}
    yield (
        "update_documents",
        {"documents": [{"document_id": 10, **value}, {"document_id": 11, **value}]},
    )
    yield "upload_document", {"content_base64": "JVBERi0xLjQgbmV3", "filename": "x.pdf", **value}
    operation: Json = {"op": "modify_custom_fields", "values": {"Choice": OPTION}}
    yield "bulk_edit_documents", {"document_ids": [10, 11], "operation": operation}
    field: Json = {"kind": "custom_field", "item": "Choice"}
    yield "update_taxonomy_item", {**field, "select_options_rename": {OPTION: "Done"}}
    yield "update_taxonomy_item", {**field, "select_options_remove": [OPTION]}


OPTION_CASES: Final[tuple[tuple[str, Json], ...]] = tuple(_options())

# The read tools that take a name, with every place for one.
READS: Final[tuple[tuple[str, str, Json], ...]] = (
    *(("search_documents", f".{name}", {name: TWIN}) for name in ONE),
    *(("search_documents", f".{name}", {name: [TWIN]}) for name in MANY),
    *(("read_documents", place, arguments) for place, arguments in _chosen_documents({})),
    ("explain_workflow", ".workflow", {"workflow": TWIN, "document_id": 10}),
    ("get_workflow", ".workflow", {"workflow": TWIN}),
    ("list_workflows", ".uses", {"uses_kind": "tag", "uses": TWIN}),
    ("get_group", ".group", {"group": TWIN}),
    ("get_user", ".user", {"user": TWIN}),
    ("get_saved_view", ".view", {"view": TWIN}),
    ("run_saved_view", ".view", {"view": TWIN}),
    ("get_task_overview", ".owner", {"owner": TWIN}),
    ("list_tasks", ".owner", {"owner": TWIN}),
    ("list_trash", ".document_type", {"document_type": TWIN}),
    ("test_storage_path", ".storage_path", {"document_id": 10, "storage_path": TWIN}),
    ("get_permissions", ".items", {"kind": "tag", "items": [TWIN]}),
)
# Places a schema shows like one for a name that take no name of an object.
NO_NAMES: Final = frozenset(
    {
        *(f".{many}.{name}" for many in ("triggers", "trigger") for name in ("type", "sources")),
        *(f".{many}.matching_algorithm" for many in ("triggers", "trigger")),
        *(f".{many}.type" for many in ("actions", "action")),
        ".rules.rule",
        ".params",  # paperless_api_get: query parameters, sent as they are
    }
)
