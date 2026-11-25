"""Document updates with merge semantics (decision-6).

Paperless replaces a document's tag and custom field lists as a whole when they are patched, so a
naive update silently drops whatever the caller did not mention. The editor reads the document
right before it writes, computes the new lists from the current ones and the requested changes,
sends only what differs, and sends nothing when the document already is as asked.

Everything predictable is checked before the first tag is created: the edit itself, the
document's change permission, names, values, linked documents and the owner. A failure after
tags were created names them.

Residual race: between the read and the PATCH (one round trip) another client may change the
same document's tags or custom fields; that change is then overwritten. Paperless has no
conditional update. Its bulk edit merges tags and custom fields on the server, but cannot set the
other fields, so using it would not make a whole update atomic either.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from mcp_server_paperless.domain.documents import (
    CustomFieldValue,
    DocumentPatch,
    DocumentState,
    DocumentUpdate,
)
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.services.custom_values import linked, require_visible, resolve_values
from mcp_server_paperless.services.principals import names_of
from mcp_server_paperless.services.tag_hierarchy import blocked_removals, close_over_ancestors
from mcp_server_paperless.services.taxonomy import fold

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.documents import DocumentEdit, DocumentRecord, NameOrId
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter, TaxonomyWriter
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.services.documents import DocumentService
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# The taxonomy kinds whose document counts depend on who owns a document.
COUNTED: tuple[TaxonomyKind, ...] = (
    "tag",
    "correspondent",
    "document_type",
    "storage_path",
    "custom_field",
)


MAX_TITLE = 128


def same_value(first: JsonValue, second: JsonValue) -> bool:
    """Equal values; ``True`` is not ``1``, and lists (document links) are compared as sets."""
    if isinstance(first, list) and isinstance(second, list):
        return sorted(map(repr, first)) == sorted(map(repr, second))
    return first == second and isinstance(first, bool) == isinstance(second, bool)


def merged_tags(current: Sequence[int], add: Iterable[int], remove: Collection[int]) -> list[int]:
    """The current tags in their order, then the added ones, without the removed ones."""
    return [tag for tag in dict.fromkeys([*current, *add]) if tag not in remove]


def merged_fields(
    current: Sequence[CustomFieldValue], sets: dict[int, JsonValue], removed: Collection[int]
) -> list[CustomFieldValue]:
    """The current custom fields in their order with ``sets`` applied and ``removed`` dropped.

    A field that is set but not yet there goes last; a field named in neither stays untouched.
    """
    kept = [
        CustomFieldValue(field=item.field, value=sets.get(item.field, item.value))
        for item in current
        if item.field not in removed
    ]
    present = {item.field for item in current}
    return kept + [
        CustomFieldValue(field=number, value=value)
        for number, value in sets.items()
        if number not in present and number not in removed
    ]


def check_edit(edit: DocumentEdit) -> None:
    """Reject empty and contradictory edits before anything is read."""
    if edit.tags_set is not None and (edit.tags_add or edit.tags_remove):
        message = "tags_set replaces all tags and cannot be combined with tags_add or tags_remove."
        raise Validation(message, hint="Use tags_set alone, or tags_add and tags_remove.")
    given = {
        "correspondent": edit.correspondent,
        "document_type": edit.document_type,
        "storage_path": edit.storage_path,
        "archive_serial_number": edit.archive_serial_number,
        "owner": edit.owner,
    }
    both = [name for name in edit.clear if given.get(name) is not None]
    if both:
        message = f"{', '.join(both)} is both set and cleared."
        raise Validation(message, hint="Pass a value or list it in clear, not both.")
    named = [{fold(str(tag)) for tag in group} for group in (edit.tags_add, edit.tags_remove)]
    if named[0] & named[1]:
        message = "A tag is both added and removed."
        raise Validation(message, hint="Name each tag once, in tags_add or in tags_remove.")
    if edit.title is not None and not edit.title.strip():
        message = "The title cannot be blank."
        raise Validation(message, hint="Pass a title, or leave it out to keep the current one.")
    if edit.title is not None and len(edit.title.strip()) > MAX_TITLE:
        message = f"The title is longer than {MAX_TITLE} characters."
        raise Validation(message, hint="Shorten the title.")
    if not (
        {name for name, value in given.items() if value is not None}
        or edit.clear
        or edit.title is not None
        or edit.created is not None
        or edit.tags_set is not None
        or edit.tags_add
        or edit.tags_remove
        or edit.custom_fields
        or edit.remove_custom_fields
    ):
        message = "Nothing to change: no field was given."
        raise Validation(message, hint="Pass at least one field to change, e.g. title or tags_add.")


# What an edit is compared with: a document as it was read.
type Current = DocumentRecord | DocumentState
# The tags created for an edit: folded name to name and id.
type Created = dict[str, tuple[str, int]]


def _numeric(value: NameOrId) -> bool:
    return isinstance(value, int) or (value.strip().isascii() and value.strip().isdigit())


@dataclass(frozen=True, slots=True)
class _Tags:
    """The tags an edit names: ids, and the names of tags to create (strings)."""

    add: list[int | str]
    remove: list[int]
    replace: list[int | str] | None
    parents: dict[int, int | None]

    @property
    def named(self) -> bool:
        """Whether the edit names tags at all."""
        return bool(self.add or self.remove) or self.replace is not None

    @property
    def new(self) -> list[str]:
        """The names of the tags to create, as often as they are named."""
        return [t for t in [*self.add, *(self.replace or [])] if isinstance(t, str)]

    def asked(self, created: Created) -> set[int]:
        """The tags the caller named to have."""
        return set(_resolved(self.replace if self.replace is not None else self.add, created))

    def change(self, current: Current, created: Created) -> dict[str, object]:
        """The new tag list, closed over the ancestors, if it differs from the current one."""
        requested = _resolved(self.replace if self.replace is not None else self.add, created)
        merged = (
            list(dict.fromkeys(requested))
            if self.replace is not None
            else merged_tags(current.tags, requested, set(self.remove))
        )
        wanted = close_over_ancestors(merged, self.parents)
        return {} if set(wanted) == set(current.tags) else {"tags": tuple(wanted)}


@dataclass(frozen=True, slots=True)
class EditPlan:
    """An edit with its names resolved and its values checked: what it asks of any state.

    Attributes:
        scalars: The plain fields it names, references as ids; ``None`` clears.
        sets: The custom field values to set, in stored form.
        removed: The custom fields to take off.
        tags: The tags it names.
    """

    scalars: dict[str, object]
    sets: dict[int, JsonValue]
    removed: set[int]
    tags: _Tags

    def patch(self, current: Current, created: Created) -> DocumentPatch:
        """What has to be sent so that ``current`` becomes as asked; empty if it already is."""
        changed = {name: v for name, v in self.scalars.items() if getattr(current, name) != v}
        return DocumentPatch.model_validate(
            {**changed, **self.tags.change(current, created), **self._field_change(current)}
        )

    def _field_change(self, current: Current) -> dict[str, object]:
        """The merged custom field list, if it differs from the current one."""
        have = {item.field: item.value for item in current.custom_fields}
        differing = {
            n: v for n, v in self.sets.items() if n not in have or not same_value(have[n], v)
        }
        dropped = self.removed & have.keys()
        if not differing and not dropped:
            return {}
        return {"custom_fields": tuple(merged_fields(current.custom_fields, differing, dropped))}


def affected(patch: DocumentPatch) -> list[TaxonomyKind]:
    """The kinds whose document counts the patch can change."""
    if "owner" in patch.model_fields_set:
        return list(COUNTED)
    by_field: dict[str, TaxonomyKind] = {
        "tags": "tag",
        "correspondent": "correspondent",
        "document_type": "document_type",
        "storage_path": "storage_path",
        "custom_fields": "custom_field",
    }
    return [kind for name, kind in by_field.items() if name in patch.model_fields_set]


def implicit(current: Current, updated: Current, plan: EditPlan, created: Created) -> list[int]:
    """The tags the document gained that nobody asked for: parents of the added children."""
    asked = plan.tags.asked(created)
    return [t for t in updated.tags if t not in current.tags and t not in asked]


def _refuse_orphans(
    current: Current, added: list[int], removed: list[int], items: dict[int, TaxonomyItem]
) -> None:
    """Refuse a removal that Paperless would extend to child tags that stay."""
    parents = {n: t.parent for n, t in items.items()}
    remaining = merged_tags(current.tags, added, removed)
    blocked = blocked_removals([t for t in removed if t in current.tags], remaining, parents)
    for tag, children in blocked.items():
        names = ", ".join(repr(items[child].name) for child in children)
        message = f"Removing {items[tag].name!r} also removes its child tags {names}."
        hint = f"List them in tags_remove too, or keep {items[tag].name!r}."
        raise Validation(message, hint=hint)


def _check_new_names(values: Iterable[int | str]) -> None:
    """Refuse tag names Paperless would reject, before the first tag is created."""
    for name in (v for v in values if isinstance(v, str)):
        if not name:
            message = "A tag to create has a blank name."
            raise Validation(message, hint="Name the tag, or leave it out.")
        if len(name) > MAX_TITLE:
            message = f"The tag name {name[:30]!r}… is longer than {MAX_TITLE} characters."
            raise Validation(message, hint="Use a shorter tag name.")


def _resolved(values: Iterable[int | str], created: Created) -> list[int]:
    return [created[fold(v)][1] if isinstance(v, str) else v for v in values]


def _after_creation(
    error: PaperlessMcpError, created: Created, *, linked: bool
) -> PaperlessMcpError:
    """``error`` for the caller: a refused link says what it needs; created tags are named."""
    hint = error.hint
    if linked and isinstance(error, PermissionDenied):
        hint = "Linking documents also changes the linked ones: you need change permission there."
        error = PermissionDenied(
            f"{error.message} A document link needs change permission on the linked document.",
            hint=hint,
        )
    if not created:
        return error
    names = ", ".join(repr(name) for name, _ in created.values())
    return type(error)(f"{error.message} Tags created before this failed: {names}.", hint=hint)


class DocumentEditor:
    """Changes a document: names to ids, a diff against its current state, merged lists."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        tag_writer: TaxonomyWriter,
        taxonomy: TaxonomyResolver,
        *,
        documents: DocumentService,
    ) -> None:
        """Read through ``reader``, write through ``writer``; ``documents`` renders the result."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._tag_writer: TaxonomyWriter = tag_writer
        self._taxonomy: TaxonomyResolver = taxonomy
        self._documents: DocumentService = documents

    async def update(self, document_id: int, edit: DocumentEdit) -> DocumentUpdate:
        """Apply ``edit`` to the document and return it as ``get_document`` would.

        Names are resolved and values checked first, so a bad name or value changes nothing.
        Missing tags are created only after that, and only with ``create_missing_tags``.

        Raises:
            Validation: The edit is empty or contradicts itself, a value does not fit, an owner
                change lacks ``confirm``, or a tag removal would take other tags along.
            NotFound: No such document, or a name or linked document matches nothing.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not change the document or create tags.
        """
        check_edit(edit)
        current = await self._reader.get_document(document_id)
        if current.user_can_change is False:
            message = "The Paperless user may not change this document."
            raise PermissionDenied(message, hint="Ask the document's owner for change permission.")
        scalars = await self._scalars(edit)
        await self._confirm_owner(edit, current, scalars)
        sets, removed, links = await self._fields(edit, current)
        await require_visible(self._reader, links)
        tags = await self._tags(edit, current)
        plan = EditPlan(scalars, sets, removed, tags)
        created = await self.create_tags(tags.new)
        try:
            patch = plan.patch(current, created)
            if not patch.model_fields_set:
                return await self._result(current, [])
            updated = await self._writer.update_document(document_id, patch)
        except PaperlessMcpError as error:
            raise _after_creation(error, created, linked=bool(sets)) from None
        for kind in affected(patch):
            self._taxonomy.invalidate(kind)
        return await self._result(updated, implicit(current, updated, plan, created))

    async def plan(self, edit: DocumentEdit, current: Current) -> EditPlan:
        """``edit`` with its names resolved, checked against ``current``; writes nothing.

        Leaves to the caller: whether the user may change the document, the confirmation, and
        whether linked documents can be seen.

        Raises:
            Validation: As for ``update``.
            NotFound: A name matches nothing.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not list what a name is looked up in.
        """
        check_edit(edit)
        scalars = await self._scalars(edit)
        sets, removed, _ = await self._fields(edit, current)
        return EditPlan(scalars, sets, removed, await self._tags(edit, current))

    async def _result(self, record: DocumentRecord, implicit: list[int]) -> DocumentUpdate:
        detail = await self._documents.detail(record)
        implicit_tags = tuple(t for t in detail.tags if t.id in implicit)
        update = DocumentUpdate.model_validate(detail, from_attributes=True)
        return update.model_copy(update={"implicit_tags": implicit_tags})

    async def _scalars(self, edit: DocumentEdit) -> dict[str, object]:
        """The plain fields the edit sets, references as ids; ``None`` clears."""
        found: dict[str, object] = {}
        references: dict[TaxonomyKind, NameOrId | None] = {
            "correspondent": edit.correspondent,
            "document_type": edit.document_type,
            "storage_path": edit.storage_path,
        }
        for kind, value in references.items():
            if value is not None:
                role = kind.replace("_", " ")
                found[kind] = (await self._taxonomy.resolve(kind, value, role=role)).id
        if edit.owner is not None:
            found["owner"] = await self._owner(edit.owner)
        plain = {
            "title": None if edit.title is None else edit.title.strip(),
            "created": edit.created,
            "archive_serial_number": edit.archive_serial_number,
        }
        found.update({name: value for name, value in plain.items() if value is not None})
        return found | dict.fromkeys(edit.clear)

    async def _confirm_owner(
        self, edit: DocumentEdit, current: DocumentRecord, scalars: dict[str, object]
    ) -> None:
        """An owner change (also to no owner) needs ``confirm``: the caller cannot undo it."""
        if edit.confirm or scalars.get("owner", current.owner) == current.owner:
            return
        owner = cast("int | None", scalars["owner"])
        document = f"document {current.id} {quoted(current.title)}"
        if owner is None:
            effect = f"remove the owner of {document}: every user who may view documents"
        else:
            name = (await names_of(self._taxonomy, "user", [owner]))[owner]
            effect = f"change the owner of {document} to {quoted(name)} (id {owner}): the new owner"
        ConfirmationRequired.require(
            f"{effect} takes it over and you may lose access to it", scope=(current.owner, owner)
        )

    async def _owner(self, value: NameOrId) -> int:
        """The user id; an id is checked against the users unless the user may not list them."""
        try:
            return (await self._taxonomy.resolve("user", value, role="owner")).id
        except PermissionDenied as error:
            if _numeric(value):
                return int(value)  # Paperless decides
            hint = "Pass the owner's user id instead of the username."
            raise PermissionDenied(error.message, hint=hint) from None

    async def _fields(
        self, edit: DocumentEdit, current: Current
    ) -> tuple[dict[int, JsonValue], set[int], set[int]]:
        """The values to set (checked, stored form), the fields to remove, the new link targets."""
        resolved = await resolve_values(self._taxonomy, edit.custom_fields)
        sets = {number: value for number, (_, value) in resolved.items()}
        have = {item.field: item.value for item in current.custom_fields}
        links = {
            n
            for number, (item, value) in resolved.items()
            if item.data_type == "documentlink"
            for n in linked(value) - linked(have.get(number))
        }
        removed = {
            (await self._taxonomy.resolve("custom_field", f, role="remove custom fields")).id
            for f in edit.remove_custom_fields
        }
        if sets.keys() & removed:
            message = "A custom field is both set and removed."
            raise Validation(message, hint="Set it or remove it, not both.")
        return sets, removed, links

    async def _tags(self, edit: DocumentEdit, current: Current) -> _Tags:
        async def ids(
            values: Iterable[NameOrId], role: str, *, create: bool, adding: bool = True
        ) -> list[int | str]:
            found: list[int | str] = []
            for value in values:
                try:
                    found.append((await self._taxonomy.resolve("tag", value, role=role)).id)
                except NotFound as error:
                    if _numeric(value) or not adding:
                        raise
                    if create:
                        found.append(str(value).strip())
                        continue
                    hint = f"{error.hint} Or set create_missing_tags=true to create it."
                    raise NotFound(error.message, hint=hint) from None
            return found

        if not (edit.tags_add or edit.tags_remove or edit.tags_set is not None):
            return _Tags(add=[], remove=[], replace=None, parents={})  # tags are not touched
        create = edit.create_missing_tags
        taken = await ids(edit.tags_remove, "tags remove", create=False, adding=False)
        removed = [t for t in taken if isinstance(t, int)]
        items = {t.id: t for t in await self._taxonomy.items("tag")}
        parents = {n: t.parent for n, t in items.items()}
        if edit.tags_set is not None:
            replace = await ids(edit.tags_set, "tags set", create=create)
            _check_new_names(replace)
            return _Tags(add=[], remove=removed, replace=replace, parents=parents)
        added = await ids(edit.tags_add, "tags add", create=create)
        if {t for t in added if isinstance(t, int)} & set(removed):
            message = "A tag is both added and removed."
            raise Validation(message, hint="Name each tag once, in tags_add or in tags_remove.")
        known = [t for t in added if isinstance(t, int)]
        _refuse_orphans(current, known, removed, items)
        _check_new_names(added)
        return _Tags(add=added, remove=removed, replace=None, parents=parents)

    async def create_tags(
        self, names: Iterable[str], *, go_on: Callable[[], bool] = lambda: True
    ) -> Created:
        """Create the tags that do not exist yet, once per name, while ``go_on``.

        Raises:
            PaperlessMcpError: One could not be created; the message names those that were.
        """
        created: Created = {}
        wanted = list(names)
        try:
            for name in wanted:
                if fold(name) not in created:
                    if not go_on():
                        break
                    try:
                        created[fold(name)] = (name, (await self._tag_writer.create_tag(name)).id)
                    except PaperlessMcpError as error:
                        raise _after_creation(error, created, linked=False) from None
        finally:
            if wanted:  # also after a failure: a lost answer may have created the tag
                self._taxonomy.invalidate("tag")
        return created
