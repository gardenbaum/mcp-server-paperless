"""Workflows: triggers and actions that Paperless runs on documents by itself.

A workflow is a name, an order, an on/off switch and two lists: the triggers (events with filters)
that start it and the actions it then applies, in order, to the matching document. Both are nested
objects with a numeric type and settings that depend on it. The port keeps them as Paperless does,
the type plus the settings under Paperless's names; ``mcp_server_paperless.services.workflows``
knows what the settings mean (and turns names into ids).
"""

from collections.abc import Mapping
from typing import Annotated, ClassVar, Final, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from mcp_server_paperless.domain.documents import NameOrId
from mcp_server_paperless.domain.tasks import Unconfirmed
from mcp_server_paperless.domain.taxonomy import TaxonomyKind  # noqa: TC001 -- NamedTuple needs it

# The types of Paperless 3.5.1 (WorkflowTrigger.WorkflowTriggerType, WorkflowAction.
# WorkflowActionType) with the labels tools use. A test compares them with the vendored schema.
TRIGGER_TYPES: Final[Mapping[int, str]] = {
    1: "consumption started",
    2: "document added",
    3: "document updated",
    4: "scheduled",
    5: "manual",
}
ACTION_TYPES: Final[Mapping[int, str]] = {
    1: "assignment",
    2: "removal",
    3: "email",
    4: "webhook",
    5: "password removal",
    6: "move to trash",
    7: "remote OCR",
    8: "apply AI suggestions",
}
# What a trigger of the consumption type can be limited to (WorkflowTriggerSource).
SOURCES: Final[Mapping[int, str]] = {
    1: "consume_folder",
    2: "api_upload",
    3: "mail_fetch",
    4: "web_ui",
}
MATCHING_ALGORITHMS: Final[Mapping[int, str]] = {
    0: "none",
    1: "any",
    2: "all",
    3: "exact",
    4: "regex",
    5: "fuzzy",
}
SCHEDULE_DATE_FIELDS: Final = ("added", "created", "modified", "custom_field")

# The types a tool may write. Password removal stores passwords this server must never echo, so it
# could neither show nor keep them; applying AI suggestions makes Paperless query its AI backend
# for every document that matches. A workflow with a manual trigger is run with run_workflow, but
# the trigger is made in the web UI. All three stay with the Paperless web UI.
WRITABLE_TRIGGER_TYPES: Final = frozenset({1, 2, 3, 4})
WRITABLE_ACTION_TYPES: Final = frozenset({1, 2, 3, 4, 6, 7})

type FieldShape = Literal["one", "list", "text", "flag", "number", "json"]


class Field_(NamedTuple):  # noqa: N801 -- a field of a trigger or action; Field is pydantic's
    """How one setting maps from the name a caller uses to Paperless's name.

    ``kind`` is the taxonomy kind whose objects the value names (by name or id), if any.
    """

    wire: str
    shape: FieldShape
    kind: TaxonomyKind | None = None


# The settings of a trigger by the name tools use. Filters keep Paperless's name without the
# ``filter_`` prefix.
TRIGGER_FIELDS: Final[Mapping[str, Field_]] = {
    "sources": Field_("sources", "list"),
    "path": Field_("filter_path", "text"),
    "filename": Field_("filter_filename", "text"),
    "mailrule": Field_("filter_mailrule", "number"),
    "match": Field_("match", "text"),
    "matching_algorithm": Field_("matching_algorithm", "number"),
    "is_insensitive": Field_("is_insensitive", "flag"),
    "has_tags": Field_("filter_has_tags", "list", "tag"),
    "has_all_tags": Field_("filter_has_all_tags", "list", "tag"),
    "has_not_tags": Field_("filter_has_not_tags", "list", "tag"),
    "has_any_correspondents": Field_("filter_has_any_correspondents", "list", "correspondent"),
    "has_not_correspondents": Field_("filter_has_not_correspondents", "list", "correspondent"),
    "has_any_document_types": Field_("filter_has_any_document_types", "list", "document_type"),
    "has_not_document_types": Field_("filter_has_not_document_types", "list", "document_type"),
    "has_any_storage_paths": Field_("filter_has_any_storage_paths", "list", "storage_path"),
    "has_not_storage_paths": Field_("filter_has_not_storage_paths", "list", "storage_path"),
    "has_correspondent": Field_("filter_has_correspondent", "one", "correspondent"),
    "has_document_type": Field_("filter_has_document_type", "one", "document_type"),
    "has_storage_path": Field_("filter_has_storage_path", "one", "storage_path"),
    "custom_field_query": Field_("filter_custom_field_query", "text"),
    "schedule_date_field": Field_("schedule_date_field", "text"),
    "schedule_date_custom_field": Field_("schedule_date_custom_field", "one", "custom_field"),
    "schedule_offset_days": Field_("schedule_offset_days", "number"),
    "schedule_is_recurring": Field_("schedule_is_recurring", "flag"),
    "schedule_recurring_interval_days": Field_("schedule_recurring_interval_days", "number"),
}
# The settings of an action by the name tools use, which is Paperless's name, grouped by the type
# of action they belong to.
ACTION_FIELDS: Final[Mapping[int, Mapping[str, Field_]]] = {
    1: {
        "assign_title": Field_("assign_title", "text"),
        "assign_tags": Field_("assign_tags", "list", "tag"),
        "assign_correspondent": Field_("assign_correspondent", "one", "correspondent"),
        "assign_document_type": Field_("assign_document_type", "one", "document_type"),
        "assign_storage_path": Field_("assign_storage_path", "one", "storage_path"),
        "assign_owner": Field_("assign_owner", "one", "user"),
        "assign_view_users": Field_("assign_view_users", "list", "user"),
        "assign_view_groups": Field_("assign_view_groups", "list", "group"),
        "assign_change_users": Field_("assign_change_users", "list", "user"),
        "assign_change_groups": Field_("assign_change_groups", "list", "group"),
        "assign_custom_fields": Field_("assign_custom_fields", "list", "custom_field"),
        "assign_custom_fields_values": Field_(
            "assign_custom_fields_values", "json", "custom_field"
        ),
    },
    2: {
        "remove_all_tags": Field_("remove_all_tags", "flag"),
        "remove_tags": Field_("remove_tags", "list", "tag"),
        "remove_all_correspondents": Field_("remove_all_correspondents", "flag"),
        "remove_correspondents": Field_("remove_correspondents", "list", "correspondent"),
        "remove_all_document_types": Field_("remove_all_document_types", "flag"),
        "remove_document_types": Field_("remove_document_types", "list", "document_type"),
        "remove_all_storage_paths": Field_("remove_all_storage_paths", "flag"),
        "remove_storage_paths": Field_("remove_storage_paths", "list", "storage_path"),
        "remove_all_custom_fields": Field_("remove_all_custom_fields", "flag"),
        "remove_custom_fields": Field_("remove_custom_fields", "list", "custom_field"),
        "remove_all_owners": Field_("remove_all_owners", "flag"),
        "remove_owners": Field_("remove_owners", "list", "user"),
        "remove_all_permissions": Field_("remove_all_permissions", "flag"),
        "remove_view_users": Field_("remove_view_users", "list", "user"),
        "remove_view_groups": Field_("remove_view_groups", "list", "group"),
        "remove_change_users": Field_("remove_change_users", "list", "user"),
        "remove_change_groups": Field_("remove_change_groups", "list", "group"),
    },
    3: {"email": Field_("email", "json")},
    4: {"webhook": Field_("webhook", "json")},
}


class WorkflowTrigger(BaseModel):
    """A trigger as Paperless stores it: its type and the settings under Paperless's names."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int | None = None
    type: int
    settings: Mapping[str, JsonValue] = {}


class WorkflowAction(BaseModel):
    """An action as Paperless stores it: its type and the settings under Paperless's names."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int | None = None
    type: int
    settings: Mapping[str, JsonValue] = {}


class Workflow(BaseModel):
    """A workflow as Paperless lists it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    order: int
    enabled: bool
    triggers: tuple[WorkflowTrigger, ...]
    actions: tuple[WorkflowAction, ...]


class WorkflowValues(BaseModel):
    """The fields of a workflow a write sets; the port sends exactly those.

    A field that was never given is left alone. ``triggers`` and ``actions`` are the whole new
    lists: Paperless replaces each list as a whole.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    order: int | None = None
    enabled: bool | None = None
    triggers: tuple[WorkflowTrigger, ...] | None = None
    actions: tuple[WorkflowAction, ...] | None = None

    @model_validator(mode="after")
    def _nothing_clears(self) -> WorkflowValues:
        """Every field has a value once given."""
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                message = f"{name} cannot be cleared"
                raise ValueError(message)
        return self


class TriggerInput(BaseModel):
    """A trigger as a caller writes it: the type by number or name, taxonomy by name or id."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    type: Annotated[
        int | str,
        Field(
            description=(
                "The trigger type: 'consumption' (1, before a document is consumed; has "
                "sources, path, filename, mailrule), 'added' (2), 'updated' (3) or "
                "'scheduled' (4; has the schedule_ settings)."
            )
        ),
    ]
    sources: Annotated[
        list[int | str] | None,
        Field(
            description=(
                "Consumption only: where documents come from, any of consume_folder, "
                "api_upload (includes the web UI upload), mail_fetch, web_ui. Default: the "
                "first three."
            )
        ),
    ] = None
    path: Annotated[
        str | None, Field(description="Consumption only: file path filter, * is a wildcard.")
    ] = None
    filename: Annotated[
        str | None, Field(description="Consumption only: file name filter, e.g. *invoice*.pdf.")
    ] = None
    mailrule: Annotated[
        int | None,
        Field(description="Consumption only: the id of a mail rule (forces mail_fetch)."),
    ] = None
    match: Annotated[str | None, Field(description="Content filter text (not consumption).")] = None
    matching_algorithm: Annotated[
        int | str | None,
        Field(description="How match applies: none, any, all, exact, regex or fuzzy."),
    ] = None
    is_insensitive: Annotated[bool | None, Field(description="Ignore case in match.")] = None
    has_tags: Annotated[
        list[NameOrId] | None, Field(description="Documents with any of these tags.")
    ] = None
    has_all_tags: Annotated[
        list[NameOrId] | None, Field(description="Documents with all of these tags.")
    ] = None
    has_not_tags: Annotated[
        list[NameOrId] | None, Field(description="Documents with none of these tags.")
    ] = None
    has_any_correspondents: Annotated[
        list[NameOrId] | None, Field(description="Documents with one of these correspondents.")
    ] = None
    has_not_correspondents: Annotated[
        list[NameOrId] | None, Field(description="Documents with none of these correspondents.")
    ] = None
    has_any_document_types: Annotated[
        list[NameOrId] | None, Field(description="Documents with one of these document types.")
    ] = None
    has_not_document_types: Annotated[
        list[NameOrId] | None, Field(description="Documents with none of these document types.")
    ] = None
    has_any_storage_paths: Annotated[
        list[NameOrId] | None, Field(description="Documents with one of these storage paths.")
    ] = None
    has_not_storage_paths: Annotated[
        list[NameOrId] | None, Field(description="Documents with none of these storage paths.")
    ] = None
    has_correspondent: Annotated[
        NameOrId | None, Field(description="Documents with this correspondent.")
    ] = None
    has_document_type: Annotated[
        NameOrId | None, Field(description="Documents with this document type.")
    ] = None
    has_storage_path: Annotated[
        NameOrId | None, Field(description="Documents with this storage path.")
    ] = None
    custom_field_query: Annotated[
        str | None,
        Field(description="A custom field query as JSON text, as in search_documents (ids)."),
    ] = None
    schedule_date_field: Annotated[
        str | None,
        Field(
            description=(
                "Scheduled only: the date to count from: added, created, modified or "
                "custom_field (then give schedule_date_custom_field)."
            )
        ),
    ] = None
    schedule_date_custom_field: Annotated[
        NameOrId | None, Field(description="Scheduled only: the date custom field, name or id.")
    ] = None
    schedule_offset_days: Annotated[
        int | None,
        Field(description="Scheduled only: days after the date (negative: before) to run."),
    ] = None
    schedule_is_recurring: Annotated[
        bool | None, Field(description="Scheduled only: run again and again.")
    ] = None
    schedule_recurring_interval_days: Annotated[
        int | None, Field(ge=1, description="Scheduled only: days between recurring runs.")
    ] = None


class EmailSettings(BaseModel):
    """What an e-mail action sends."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    subject: Annotated[
        str, Field(min_length=1, max_length=256, description="May use placeholders.")
    ]
    body: Annotated[str, Field(min_length=1, description="May use placeholders.")]
    to: Annotated[str, Field(min_length=1, description="Addresses, comma separated.")]
    include_document: Annotated[bool, Field(description="Attach the document.")] = False


class WebhookSettings(BaseModel):
    """What a webhook action sends: a POST request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=256,
            description=(
                "The URL to POST to. get_workflow shows it without user name and password; send "
                "that back with the action's id to keep them, or with an empty user name "
                "(https://@host/path) to remove them."
            ),
        ),
    ]
    use_params: Annotated[
        bool, Field(description="Send params as key-value pairs instead of the body.")
    ] = True
    as_json: Annotated[bool, Field(description="Send as JSON instead of form data.")] = False
    params: Annotated[
        dict[str, str] | None, Field(description="Key-value pairs to send; may use placeholders.")
    ] = None
    body: Annotated[
        str | None, Field(description="Text to send instead of params; may use placeholders.")
    ] = None
    headers: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "Request headers. Values are never shown again (they can hold tokens): "
                "get_workflow shows '(hidden)'; send that back with the action's id to keep the "
                "stored value."
            )
        ),
    ] = None
    include_document: Annotated[bool, Field(description="Send the document too.")] = False


class ActionInput(BaseModel):
    """An action as a caller writes it: the type by number or name, taxonomy by name or id."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    type: Annotated[
        int | str,
        Field(
            description=(
                "The action type: 'assignment' (1), 'removal' (2), 'email' (3), 'webhook' (4), "
                "'move to trash' (6) or 'remote OCR' (7, consumption triggers only). Settings of "
                "another type than this one are an error."
            )
        ),
    ]
    id: Annotated[
        int | None,
        Field(description="The id the action has now (from get_workflow), to keep hidden values."),
    ] = None
    assign_title: Annotated[
        str | None, Field(description="A Jinja template for the title, e.g. '{{ correspondent }}'.")
    ] = None
    assign_tags: Annotated[list[NameOrId] | None, Field(description="Tags to add.")] = None
    assign_correspondent: Annotated[NameOrId | None, Field(description="Correspondent.")] = None
    assign_document_type: Annotated[NameOrId | None, Field(description="Document type.")] = None
    assign_storage_path: Annotated[NameOrId | None, Field(description="Storage path.")] = None
    assign_owner: Annotated[NameOrId | None, Field(description="Owner, a user name or id.")] = None
    assign_view_users: Annotated[
        list[NameOrId] | None, Field(description="Users granted view permission.")
    ] = None
    assign_view_groups: Annotated[
        list[NameOrId] | None, Field(description="Groups granted view permission.")
    ] = None
    assign_change_users: Annotated[
        list[NameOrId] | None, Field(description="Users granted change permission.")
    ] = None
    assign_change_groups: Annotated[
        list[NameOrId] | None, Field(description="Groups granted change permission.")
    ] = None
    assign_custom_fields: Annotated[
        list[NameOrId] | None, Field(description="Custom fields to add, without a value.")
    ] = None
    assign_custom_fields_values: Annotated[
        dict[str, JsonValue] | None,
        Field(description="Custom field name or id -> the value to set (overwrites)."),
    ] = None
    remove_all_tags: Annotated[bool | None, Field(description="Remove every tag.")] = None
    remove_tags: Annotated[list[NameOrId] | None, Field(description="Tags to remove.")] = None
    remove_all_correspondents: Annotated[bool | None, Field(description="Clear it.")] = None
    remove_correspondents: Annotated[
        list[NameOrId] | None, Field(description="Remove when it is one of these.")
    ] = None
    remove_all_document_types: Annotated[bool | None, Field(description="Clear it.")] = None
    remove_document_types: Annotated[
        list[NameOrId] | None, Field(description="Remove when it is one of these.")
    ] = None
    remove_all_storage_paths: Annotated[bool | None, Field(description="Clear it.")] = None
    remove_storage_paths: Annotated[
        list[NameOrId] | None, Field(description="Remove when it is one of these.")
    ] = None
    remove_all_custom_fields: Annotated[
        bool | None, Field(description="Remove every custom field.")
    ] = None
    remove_custom_fields: Annotated[
        list[NameOrId] | None, Field(description="Custom fields to remove.")
    ] = None
    remove_all_owners: Annotated[bool | None, Field(description="Clear the owner.")] = None
    remove_owners: Annotated[
        list[NameOrId] | None, Field(description="Clear the owner when it is one of these users.")
    ] = None
    remove_all_permissions: Annotated[
        bool | None, Field(description="Remove every view and change grant.")
    ] = None
    remove_view_users: Annotated[
        list[NameOrId] | None, Field(description="Users to remove view permission from.")
    ] = None
    remove_view_groups: Annotated[
        list[NameOrId] | None, Field(description="Groups to remove view permission from.")
    ] = None
    remove_change_users: Annotated[
        list[NameOrId] | None, Field(description="Users to remove change permission from.")
    ] = None
    remove_change_groups: Annotated[
        list[NameOrId] | None, Field(description="Groups to remove change permission from.")
    ] = None
    email: Annotated[EmailSettings | None, Field(description="For type email: what to send.")] = (
        None
    )
    webhook: Annotated[
        WebhookSettings | None, Field(description="For type webhook: what to send.")
    ] = None


class WorkflowCreate(BaseModel):
    """A new workflow."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    triggers: tuple[TriggerInput, ...] = ()
    actions: tuple[ActionInput, ...] = ()
    enabled: bool = True
    order: int | None = None


class WorkflowEdit(BaseModel):
    """Changes to a workflow; what is left out stays. ``triggers`` and ``actions`` replace all."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    triggers: tuple[TriggerInput, ...] | None = None
    actions: tuple[ActionInput, ...] | None = None
    enabled: bool | None = None
    order: int | None = None


class TriggerInfo(BaseModel):
    """A trigger as tools show it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int | None
    type: int
    type_name: Annotated[str, Field(description="The label of the type, e.g. 'document added'.")]
    settings: Annotated[
        dict[str, JsonValue],
        Field(
            description="The settings that are not at their default, named as create takes them."
        ),
    ]


class ActionInfo(BaseModel):
    """An action as tools show it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int | None
    type: int
    type_name: Annotated[str, Field(description="The label of the type, e.g. 'assignment'.")]
    settings: Annotated[
        dict[str, JsonValue],
        Field(description="The settings that are not empty, named as create takes them."),
    ]
    shown_in_part: Annotated[
        tuple[str, ...],
        Field(
            description=(
                "webhook.url if it has, or may have, credentials, which are not shown (nor the "
                "URL, if they cannot be told apart from the rest). Writing it back as shown, with "
                "this id, keeps what is stored."
            )
        ),
    ] = ()


class WorkflowInfo(BaseModel):
    """A workflow as tools show it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    order: Annotated[int, Field(description="Workflows run in this order, lowest first.")]
    enabled: bool
    triggers: tuple[TriggerInfo, ...]
    actions: tuple[ActionInfo, ...]
    note: Annotated[
        str | None,
        Field(
            description=(
                "After a change: which webhook URLs lost or kept their credentials, and how to get "
                "back what a move to trash deletes."
            )
        ),
    ] = None


class WorkflowListing(BaseModel):
    """The workflows the user may see."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    workflows: tuple[WorkflowInfo, ...]


class WorkflowDeletion(BaseModel):
    """The outcome of a confirmed delete_workflow."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    message: Annotated[str, Field(description="What was deleted.")]


class WorkflowRun(BaseModel):
    """A run of a workflow on documents, as far as its queued task tells.

    Attributes:
        task_id: The queued task.
        failed: The documents the workflow failed on; some of its actions may have run there.
        unconfirmed: Set when the task could not be followed to its end.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: str
    failed: tuple[int, ...] = ()
    unconfirmed: Unconfirmed | None = None


class WorkflowRunReport(BaseModel):
    """A run of a workflow by hand: the preview of a dry run, or what was asked of Paperless."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    workflow_id: int
    workflow: Annotated[str, Field(description="Its name.")]
    actions: Annotated[
        tuple[str, ...],
        Field(description="What it does to each document, the most far-reaching first."),
    ]
    apply_filters: bool
    dry_run: Annotated[bool, Field(description="true: a preview, nothing was run.")]
    started: Annotated[bool, Field(description="true: Paperless was asked to run it.")]
    unconfirmed_task_id: Annotated[
        str | None,
        Field(
            description=(
                "Set when the end of the run could not be observed: it may still be running or "
                "may have failed. Read it with get_task; do not run the workflow again."
            )
        ),
    ] = None
    matched: Annotated[
        int,
        Field(
            description="How many documents the ids, filters or view match, exclude_ids included."
        ),
    ]
    selected_ids: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "The documents it is run on; after a preview with filters or saved_view they are "
                "the document_ids of the next call."
            )
        ),
    ]
    also_matching: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "With document_ids and filters or saved_view: what matches besides and is not "
                "touched."
            )
        ),
    ] = ()
    failed: Annotated[
        tuple[int, ...],
        Field(description="Documents it failed on; some of its actions may have run there."),
    ] = ()
    blocked: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "Documents you may not change or do not own. While there are any, the whole run "
                "is refused."
            )
        ),
    ] = ()
    message: str
