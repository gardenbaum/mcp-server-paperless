"""Saved views: found by name or id (decision-5), written with rules by label and name, run.

A view is run by translating its filter rules into the same ``DocumentQuery`` the search tool
builds and searching with it, so a view returns the rows and paging of ``search_documents``.
Writes act on the views read right before, not on the resolver's cache, and forget the resolver's
saved views afterwards, whatever happened.
"""

import contextlib
import difflib
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import NameFilters
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.taxonomy import TaxonomyItem
from mcp_server_paperless.domain.views import (
    DISPLAY_FIELDS,
    ICONS,
    MAX_DISPLAY_FIELDS,
    RULE_TYPES,
    DisplayField,
    FilterRule,
    RuleInfo,
    SavedViewDetail,
    SavedViewInfo,
    SavedViewListing,
    ViewDeletion,
    ViewValues,
    display_field_id,
)
from mcp_server_paperless.services.access import read_access
from mcp_server_paperless.services.filter_rules import check_sort, translate
from mcp_server_paperless.services.taxonomy import fold, pick
from mcp_server_paperless.services.view_pins import DASHBOARD, SIDEBAR, pinned

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from mcp_server_paperless.domain.documents import DocumentQuery, DocumentSearchResult, NameOrId
    from mcp_server_paperless.domain.ports import PermissionReader, ViewReader, ViewWriter
    from mcp_server_paperless.domain.taxonomy import TaxonomyKind
    from mcp_server_paperless.domain.views import (
        PinnedView,
        RuleInput,
        SavedView,
        ViewCreate,
        ViewEdit,
    )
    from mcp_server_paperless.services.documents import DocumentService
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver
    from mcp_server_paperless.services.view_pins import Settings, ViewPins

# The taxonomy kind whose objects the value of each rule type names.
VALUE_KINDS: Final[Mapping[int, TaxonomyKind]] = {
    **dict.fromkeys((6, 17, 22), "tag"),
    **dict.fromkeys((3, 26, 27), "correspondent"),
    **dict.fromkeys((4, 28, 29), "document_type"),
    **dict.fromkeys((25, 30, 31), "storage_path"),
    **dict.fromkeys((38, 39, 40), "custom_field"),
    **dict.fromkeys((32, 33, 35), "user"),
}
# "Correspondent is" and its siblings: no value is "not assigned", -1 is "assigned".
NULLABLE: Final = frozenset({3, 4, 25})
SUGGESTIONS: Final = 5
# The settings of the web UI beside the page size.
DISPLAY: Final = frozenset({"icon", "display_mode", "display_fields"})
# The settings that can be removed, and the argument that says so.
CLEARED_BY: Final[Mapping[str, str]] = {
    "sort_field": "clear_sort",
    "page_size": "clear_page_size",
    "display_mode": "clear_display_mode",
    "display_fields": "clear_display_fields",
}


def plural(count: int, noun: str) -> str:
    """``count`` and ``noun`` with an s unless there is exactly one."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _described(view: SavedView) -> str:
    return f"saved view {quoted(view.name)} (id {view.id})"


def info(
    view: SavedView, field_names: Mapping[int, str], settings: Settings | None
) -> SavedViewInfo:
    """The view as tools show it, with labels, names of custom fields and where it is pinned."""
    fields = view.display_fields
    return SavedViewInfo(
        id=view.id,
        name=view.name,
        sort_field=view.sort_field,
        sort_reverse=view.sort_reverse,
        page_size=view.page_size,
        icon=view.icon,
        display_mode=view.display_mode,
        display_fields=None
        if fields is None
        else tuple(
            DisplayField(field=f, name=field_names.get(display_field_id(f) or 0)) for f in fields
        ),
        on_dashboard=None if settings is None else pinned(settings, DASHBOARD, view.id),
        in_sidebar=None if settings is None else pinned(settings, SIDEBAR, view.id),
        owner=view.owner,
        can_change=view.user_can_change,
        rules=tuple(
            RuleInfo(
                rule_type=rule.rule_type,
                name=RULE_TYPES.get(rule.rule_type, f"unknown rule type {rule.rule_type}"),
                value=rule.value,
            )
            for rule in view.filter_rules
        ),
    )


def cleared(edit: ViewEdit) -> dict[str, None]:
    """The settings ``edit`` removes.

    Raises:
        Validation: A setting is given and removed at once.
    """
    found: dict[str, None] = {}
    for name, clear in CLEARED_BY.items():
        if getattr(edit, clear):
            if getattr(edit, name) is not None:
                message = f"{name} and {clear} contradict each other."
                raise Validation(message, hint="Pass one of them.")
            found[name] = None
    return found


def rule_type(value: int | str) -> int:
    """The rule type number that ``value`` (a number, digits or a label) names.

    Raises:
        Validation: No rule type has that number or label; the hint lists the closest labels.
    """
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        return int(text)
    by_label = {fold(label): number for number, label in RULE_TYPES.items()}
    if fold(text) in by_label:
        return by_label[fold(text)]
    close = difflib.get_close_matches(fold(text), by_label, n=SUGGESTIONS)
    names = ", ".join(repr(RULE_TYPES[by_label[key]]) for key in close)
    hint = f"Did you mean {names}? " if names else ""
    message = f"No filter rule type is called {text!r}."
    raise Validation(
        message, hint=f"{hint}Use a number from 0 to 50 or a label that create_saved_view lists."
    )


class ViewService:
    """Reads, writes and runs the saved views of the current user."""

    def __init__(  # noqa: PLR0913 -- the ports and services it uses
        self,
        reader: ViewReader,
        writer: ViewWriter,
        taxonomy: TaxonomyResolver,
        documents: DocumentService,
        pins: ViewPins,
        *,
        permissions: PermissionReader,
    ) -> None:
        """Read through ``reader``, write through ``writer``, search with ``documents``.

        ``pins`` knows what the user pinned, ``permissions`` reads owner and grants of one view.
        """
        self._reader: ViewReader = reader
        self._writer: ViewWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy
        self._documents: DocumentService = documents
        self._pins: ViewPins = pins
        self._permissions: PermissionReader = permissions

    async def list(self) -> SavedViewListing:
        """Every saved view the user may see, with its rules.

        Raises:
            PermissionDenied: The user may not view saved views.
        """
        views = await self._reader.list_saved_views()
        return SavedViewListing(count=len(views), views=await self._infos(*views))

    async def get(self, view: NameOrId) -> SavedViewDetail:
        """The view that ``view`` (a name or an id) names, with its owner and grants.

        Raises:
            NotFound: No such view; the hint lists the closest names.
            Ambiguous: The name matches several views.
            PermissionDenied: The user may not view saved views.
        """
        found = await self._find(view)
        [shown] = await self._infos(found)
        try:
            access = await read_access(self._permissions, self._taxonomy, "saved_view", found.id)
        except PermissionDenied:
            raise
        except PaperlessMcpError:
            access = None
        return SavedViewDetail.model_validate({**shown.model_dump(), "access": access})

    async def create(self, spec: ViewCreate) -> SavedViewInfo:
        """Create a view; rules are checked as ``run`` would read them before anything is sent.

        Raises:
            Validation: The name is empty or taken, or a rule or the sort field is unusable.
            NotFound: A name in a rule matches nothing.
            Ambiguous: A name in a rule matches several objects.
            PermissionDenied: The user may not add saved views.
        """
        name = self._name(spec.name)
        await self._free(name, except_id=None)
        rules = await self._rules(spec.rules)
        _ = translate(rules)
        if spec.sort_field is not None:
            check_sort(spec.sort_field)
        await self._check_display(spec)
        settings: dict[str, object] = {
            "filter_rules": rules,
            "sort_field": spec.sort_field,
            "sort_reverse": spec.sort_reverse,
            "page_size": spec.page_size,
        }
        display = spec.model_dump(include=set(DISPLAY), exclude_none=True)
        values = ViewValues.model_validate(settings | display)
        made = await self._write(lambda: self._writer.create_saved_view(name, values))
        [shown] = await self._infos(made)
        return shown

    async def update(self, view: NameOrId, edit: ViewEdit) -> SavedViewInfo:
        """Change the named settings of a view; new rules replace all old ones.

        Raises:
            Validation: Nothing to change, a contradiction, or an unusable name, rule or sort.
            NotFound: No such view, or a name in a rule matches nothing.
            Ambiguous: A name matches several views or objects.
            PermissionDenied: The user may not change saved views, or this one.
        """
        removed = cleared(edit)
        found = self._changeable(await self._find(view))
        changes: dict[str, object] = {}
        if edit.name is not None:
            changes["name"] = self._name(edit.name)
            await self._free(str(changes["name"]), except_id=found.id)
        if edit.rules is not None:
            changes["filter_rules"] = await self._rules(edit.rules)
        kept = {"sort_field", "sort_reverse", "page_size", *DISPLAY}
        changes |= edit.model_dump(include=kept, exclude_none=True) | removed
        if not changes:
            message = "Nothing to change: no setting was named."
            raise Validation(message, hint="Name a setting to change or to clear.")
        values = ViewValues.model_validate(changes)
        # Only what the edit sets is checked: a view with a bad sort can still be renamed.
        _ = translate(values.filter_rules or ())
        if edit.sort_field is not None:
            check_sort(edit.sort_field)
        await self._check_display(edit)
        made = await self._write(lambda: self._writer.update_saved_view(found.id, values))
        [shown] = await self._infos(made)
        return shown

    async def pin(
        self, view: NameOrId, *, dashboard: bool | None, sidebar: bool | None
    ) -> PinnedView:
        """Put a view the user sees on the dashboard or into the sidebar; see ``ViewPins.pin``.

        Raises:
            NotFound: No such view.
            Ambiguous: The name matches several views.
        """
        return await self._pins.pin(await self._find(view), dashboard=dashboard, sidebar=sidebar)

    async def delete(self, view: NameOrId, *, confirm: bool) -> ViewDeletion:
        """Delete a view for good; without ``confirm`` refuse and say what deleting it does.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such view.
            Ambiguous: The name matches several views.
            PermissionDenied: The user may not delete saved views, or this one.
        """
        found = self._changeable(await self._find(view))
        subject = _described(found)
        if not confirm:
            rules = plural(len(found.filter_rules), "filter rule")
            effect = f"delete {subject} for good: its {rules} and its sort order are lost"
            effect = f"{effect}; no document is touched"
            ConfirmationRequired.require(effect, scope=found.filter_rules)
        await self._write(lambda: self._writer.delete_saved_view(found.id))
        message = f"{subject[0].upper()}{subject[1:]} was deleted; no document was touched."
        return ViewDeletion(id=found.id, name=found.name, message=message)

    async def run(self, view: NameOrId, *, page: int, page_size: int) -> DocumentSearchResult:
        """One page of the documents of a view, as ``search_documents`` would return them.

        Raises:
            Validation: A rule or the sort field of the view cannot be run; the message names
                the rule type.
            NotFound: No such view, or ``page`` is beyond the last page.
            Ambiguous: The name matches several views.
            PermissionDenied: The user may not view saved views or documents.
        """
        query = await self.query(view)
        return await self._documents.search(NameFilters(), query, page=page, page_size=page_size)

    async def query(self, view: NameOrId) -> DocumentQuery:
        """The search a view stands for; nothing is searched.

        Raises:
            Validation: A rule or the sort field of the view cannot be run.
            NotFound: No such view.
            Ambiguous: The name matches several views.
            PermissionDenied: The user may not view saved views.
        """
        found = await self._find(view)
        return translate(found.filter_rules, found.sort_field, sort_reverse=found.sort_reverse)

    async def _infos(self, *views: SavedView) -> tuple[SavedViewInfo, ...]:
        """The views as tools show them; custom fields are only looked up if a view shows one."""
        shown = [field for view in views for field in view.display_fields or ()]
        names: dict[int, str] = {}
        if any(display_field_id(field) for field in shown):
            # What was written stays written: a failed lookup costs only the names.
            with contextlib.suppress(PaperlessMcpError):
                names = {item.id: item.name for item in await self._taxonomy.items("custom_field")}
        settings = await self._pins.settings()
        return tuple(info(view, names, settings) for view in views)

    async def _check_display(self, given: ViewCreate | ViewEdit) -> None:
        """Refuse an icon or display fields Paperless rejects, fails on or stores for nothing."""
        if given.icon is not None and given.icon not in ICONS:
            message = f"No icon of saved views is called {quoted(given.icon)}."
            raise Validation(message, hint=f"Use one of {', '.join(ICONS)}.")
        fields = given.display_fields
        if fields is None:
            return
        if not fields:
            message = "display_fields is empty."
            raise Validation(message, hint="Name fields; update_saved_view can clear them.")
        if len(fields) > MAX_DISPLAY_FIELDS:
            message = f"{len(fields)} display fields are more than {MAX_DISPLAY_FIELDS}."
            raise Validation(message, hint="Name fewer fields.")
        for position, field in enumerate(fields):
            if field in fields[:position]:
                message = f"display_fields names {field!r} more than once."
                raise Validation(message, hint="Name each field once.")
            number = display_field_id(field)
            if number is not None:
                _ = await self._taxonomy.resolve("custom_field", number)
            elif field not in DISPLAY_FIELDS:
                message = f"No display field is called {quoted(field)}."
                raise Validation(
                    message, hint=f"Use {', '.join(DISPLAY_FIELDS)} or custom_field_<id>."
                )

    async def _find(self, view: NameOrId) -> SavedView:
        views = await self._reader.list_saved_views()
        rows = [
            TaxonomyItem(id=v.id, name=v.name, document_count=None, owner=v.owner) for v in views
        ]
        try:
            wanted = pick("saved_view", rows, view).id
        except NotFound as error:
            # The toolset may be on without list_taxonomy: point at its own list.
            hint = error.hint and error.hint.replace(
                "list_taxonomy(kind='saved_view')", "list_saved_views"
            )
            raise NotFound(error.message, hint=hint) from None
        return next(v for v in views if v.id == wanted)

    @staticmethod
    def _changeable(view: SavedView) -> SavedView:
        """``view`` if the user may change it; else the refusal, before any confirmation step."""
        if not view.user_can_change:
            message = f"The user may not change {_described(view)}: it belongs to someone else."
            raise PermissionDenied(message, hint="Ask its owner, or create a view of your own.")
        return view

    @staticmethod
    def _name(name: str) -> str:
        text = name.strip()
        if not text:
            message = "The view name is empty."
            raise Validation(message, hint="Give the view a name.")
        return text

    async def _free(self, name: str, *, except_id: int | None) -> None:
        """Refuse a name another visible view has (ignoring case): names find views."""
        for view in await self._reader.list_saved_views():
            if view.id != except_id and fold(view.name) == fold(name):
                message = (
                    f"A saved view you can see is already named {quoted(view.name)} (id {view.id})."
                )
                raise Validation(message, hint="Choose another name, or update that view.")

    async def _rules(self, rules: Sequence[RuleInput]) -> tuple[FilterRule, ...]:
        return tuple([await self._rule(rule) for rule in rules])

    async def _rule(self, spec: RuleInput) -> FilterRule:
        """The stored rule: type by number, value as text, a name looked up as an id."""
        number = rule_type(spec.rule)
        value = spec.value
        if isinstance(value, bool):
            return FilterRule(rule_type=number, value="true" if value else "false")
        if value is None or (number in NULLABLE and str(value) == "-1"):
            return FilterRule(rule_type=number, value=None if value is None else str(value))
        text = str(value).strip()
        kind = VALUE_KINDS.get(number)
        if kind is not None:
            # The web UI joins the values of a list rule with commas; each part is an id or name.
            parts = [part.strip() for part in text.split(",")] if number not in NULLABLE else [text]
            text = ",".join([await self._id(kind, part) for part in parts if part])
        return FilterRule(rule_type=number, value=text)

    async def _id(self, kind: TaxonomyKind, part: str) -> str:
        if part.isascii() and part.isdigit():
            return part
        return str((await self._taxonomy.resolve(kind, part)).id)

    async def _write[T](self, write: Callable[[], Awaitable[T]]) -> T:
        try:
            return await write()
        finally:
            self._taxonomy.invalidate("saved_view")
