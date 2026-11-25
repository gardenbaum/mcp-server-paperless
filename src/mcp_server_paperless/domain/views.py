"""Saved views: filter rules over documents, as Paperless stores them and as tools show them.

A saved view is a name, a sort order and a list of filter rules, each a numeric rule type and a
text value. Paperless itself never runs a view: the web UI turns the rules into a document query,
and so does ``run_saved_view`` (``mcp_server_paperless.services.filter_rules``).
"""

import re
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mcp_server_paperless.domain.permissions import AccessView

if TYPE_CHECKING:
    from collections.abc import Mapping

# Every rule type of Paperless (SavedViewFilterRule.RULE_TYPES) with the label the web UI
# uses. A test compares the table with the vendored OpenAPI schema.
RULE_TYPES: Final[Mapping[int, str]] = {
    0: "title contains",
    1: "content contains",
    2: "ASN is",
    3: "correspondent is",
    4: "document type is",
    5: "is in inbox",
    6: "has tag",
    7: "has any tag",
    8: "created before",
    9: "created after",
    10: "created year is",
    11: "created month is",
    12: "created day is",
    13: "added before",
    14: "added after",
    15: "modified before",
    16: "modified after",
    17: "does not have tag",
    18: "does not have ASN",
    19: "title or content contains",
    20: "fulltext query",
    21: "more like this",
    22: "has tags in",
    23: "ASN greater than",
    24: "ASN less than",
    25: "storage path is",
    26: "has correspondent in",
    27: "does not have correspondent in",
    28: "has document type in",
    29: "does not have document type in",
    30: "has storage path in",
    31: "does not have storage path in",
    32: "owner is",
    33: "has owner in",
    34: "does not have owner",
    35: "does not have owner in",
    36: "has custom field value",
    37: "is shared by me",
    38: "has custom fields",
    39: "has custom field in",
    40: "does not have custom field in",
    41: "does not have custom field",
    42: "custom fields query",
    43: "created to",
    44: "created from",
    45: "added to",
    46: "added from",
    47: "mime type is",
    48: "simple title search",
    49: "simple text search",
    50: "has duplicates",
}

# The icons of 3.5.1 (a test compares them with the vendored OpenAPI schema).
ICONS: Final = (
    "archive",
    "bank",
    "basket",
    "bell",
    "bookmark",
    "boxes",
    "briefcase",
    "building",
    "calculator",
    "calendar",
    "camera",
    "card-checklist",
    "cash",
    "chat-left-text",
    "check-circle",
    "clipboard",
    "clock-history",
    "credit-card",
    "download",
    "envelope",
    "exclamation-triangle",
    "file-earmark",
    "file-earmark-check",
    "file-earmark-lock",
    "file-earmark-medical",
    "file-earmark-person",
    "file-earmark-spreadsheet",
    "file-text",
    "files",
    "folder",
    "funnel",
    "gear",
    "globe2",
    "hash",
    "heart",
    "house",
    "inbox",
    "journals",
    "list-task",
    "newspaper",
    "paperclip",
    "people",
    "person",
    "printer",
    "receipt",
    "safe",
    "search",
    "send",
    "shop",
    "stack",
    "stars",
    "tag",
    "tags",
    "telephone",
    "truck",
    "upc-scan",
    "wallet2",
)
DEFAULT_ICON: Final = "funnel"
type DisplayMode = Literal["table", "smallCards", "largeCards"]
# SavedView.DisplayFields of 3.5.1 (the schema leaves them open) beside ``custom_field_<id>``.
DISPLAY_FIELDS: Final = (
    *("title", "created", "added", "tag", "correspondent", "documenttype", "storagepath"),
    *("note", "owner", "shared", "asn", "pagecount"),
)
MAX_DISPLAY_FIELDS: Final = 50
_CUSTOM_FIELD: Final = re.compile(r"custom_field_([1-9][0-9]{0,8})")


def display_field_id(field: str) -> int | None:
    """The id of the custom field that ``field`` names in exactly the form Paperless writes.

    Paperless itself looks for the text anywhere and fails without a number (PLMCP-88.1, P4).
    """
    found = _CUSTOM_FIELD.fullmatch(field)
    return None if found is None else int(found[1])


class FilterRule(BaseModel):
    """One filter rule as Paperless stores it: the rule type and its value as text."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    rule_type: int
    value: str | None = None


class SavedView(BaseModel):
    """A saved view as Paperless lists it.

    ``display_fields`` is ``None`` where Paperless has none or something else than a list.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    sort_field: str | None
    sort_reverse: bool
    page_size: int | None
    icon: str
    display_mode: str | None
    display_fields: tuple[str, ...] | None
    owner: int | None
    user_can_change: bool
    filter_rules: tuple[FilterRule, ...]


class ViewValues(BaseModel):
    """The fields of a saved view a write sets; the port sends exactly those.

    A field that was never given is left alone. ``filter_rules`` is the whole new list.
    ``sort_field``, ``page_size``, ``display_mode`` and ``display_fields`` can be given as
    ``None`` (no sort, the defaults of the web UI).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    filter_rules: tuple[FilterRule, ...] | None = None
    sort_field: str | None = None
    sort_reverse: bool | None = None
    page_size: int | None = None
    icon: str | None = None
    display_mode: DisplayMode | None = None
    display_fields: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _only_some_fields_clear(self) -> ViewValues:
        """Name, rules, sort direction and icon have a value once given."""
        clearable = {"sort_field", "page_size", "display_mode", "display_fields"}
        for name in self.model_fields_set - clearable:
            if getattr(self, name) is None:
                message = f"{name} cannot be cleared"
                raise ValueError(message)
        return self


class RuleInput(BaseModel):
    """A filter rule as a caller writes it: the type by number or label, the value by id or name."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    rule: Annotated[
        int | str,
        Field(description="The rule type: its number (0 to 50) or its label, e.g. 'has tag'."),
    ]
    value: Annotated[
        str | int | bool | None,
        Field(
            description=(
                "The value: a name or id for tags, correspondents, document types, storage "
                "paths, custom fields and owners; a date (YYYY-MM-DD); a number; true or "
                "false; or text, as the rule type needs. Null where it needs none."
            )
        ),
    ] = None


class ViewCreate(BaseModel):
    """A new saved view."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    rules: tuple[RuleInput, ...] = ()
    sort_field: str | None = None
    sort_reverse: bool = False
    page_size: int | None = None
    icon: str | None = None
    display_mode: DisplayMode | None = None
    display_fields: tuple[str, ...] | None = None


class ViewEdit(BaseModel):
    """Changes to a saved view; what is left out stays as it is. ``rules`` replaces all rules."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    rules: tuple[RuleInput, ...] | None = None
    sort_field: str | None = None
    sort_reverse: bool | None = None
    page_size: int | None = None
    icon: str | None = None
    display_mode: DisplayMode | None = None
    display_fields: tuple[str, ...] | None = None
    clear_sort: bool = False
    clear_page_size: bool = False
    clear_display_mode: bool = False
    clear_display_fields: bool = False


class RuleInfo(BaseModel):
    """A filter rule with the label of its type."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    rule_type: int
    name: Annotated[str, Field(description="The label of the rule type, e.g. 'has tag'.")]
    value: Annotated[str | None, Field(description="The value as stored, usually an id.")]


class DisplayField(BaseModel):
    """A document field a view shows."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    field: Annotated[str, Field(description="As stored, e.g. 'title' or 'custom_field_3'.")]
    name: Annotated[
        str | None, Field(description="The name of a custom field where it can be read.")
    ]


class SavedViewInfo(BaseModel):
    """A saved view as tools show it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    sort_field: Annotated[str | None, Field(description="The field it sorts by, null: default.")]
    sort_reverse: Annotated[bool, Field(description="Descending when sort_field is set.")]
    page_size: Annotated[int | None, Field(description="The page size of the web UI, if set.")]
    icon: str
    display_mode: Annotated[
        str | None, Field(description="table, smallCards or largeCards; null: the default.")
    ]
    display_fields: Annotated[
        tuple[DisplayField, ...] | None,
        Field(description="What the web UI shows of each document; null: the default."),
    ]
    on_dashboard: Annotated[
        bool | None, Field(description="Of the current user; null: cannot be read.")
    ]
    in_sidebar: Annotated[
        bool | None, Field(description="Of the current user; null: cannot be read.")
    ]
    owner: Annotated[int | None, Field(description="User id of the owner; null: shared by all.")]
    can_change: Annotated[bool, Field(description="Whether the current user may change it.")]
    rules: tuple[RuleInfo, ...]


class SavedViewDetail(SavedViewInfo):
    """One saved view with its owner and grants."""

    access: Annotated[
        AccessView | None, Field(description="Change it with set_permissions (kind saved_view).")
    ]


class SavedViewListing(BaseModel):
    """The saved views the user may see."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    views: tuple[SavedViewInfo, ...]


class ViewDeletion(BaseModel):
    """The outcome of a confirmed delete_saved_view."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    message: Annotated[str, Field(description="What was deleted.")]


class PinnedView(BaseModel):
    """Where the current user has a saved view after pin_saved_view."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    on_dashboard: bool
    in_sidebar: bool
    changed: Annotated[bool, Field(description="false: it already was as asked.")]
    message: str
