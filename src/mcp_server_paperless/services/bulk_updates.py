"""Values of their own for many documents: checked as a whole, then one PATCH per document.

Phase 1 reads every document and linked document in list requests, resolves all names and refuses
the whole call, naming every wrong item, before anything is written. One confirmation covers the
call (decision-73). Phase 2 goes document by document in the order given: each is read again, is
written only if the values this call touches are still the ones that were checked (and shown), and
ends in exactly one status. Links that earlier items of the call mirrored count as expected.

Paperless mirrors document links: a PATCH of the custom fields adds the reverse link on every
document in a link field of the whole list that is sent, and takes it off the ones that were
dropped. It refuses the PATCH if one of the linked documents cannot be changed by the user.

Residual race: between the second read and the PATCH another client may change the same document;
Paperless has no conditional update. Not atomic across documents.
"""

import itertools
import json
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Final, cast

from mcp_server_paperless.domain.bulk import MAX_DOCUMENTS
from mcp_server_paperless.domain.bulk_items import (
    UpdatedDocument,
    UpdateReport,
    UpdateStatus,
    ValueChange,
)
from mcp_server_paperless.domain.documents import (
    CustomFieldValue,
    DocumentPatch,
    DocumentState,
    Ref,
)
from mcp_server_paperless.domain.errors import (
    Ambiguous,
    AnswerTooLarge,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.selection import MAX_ANSWER
from mcp_server_paperless.services.bulk_documents import DRY_RUN, READ_AGAIN, TO_GO_AHEAD
from mcp_server_paperless.services.bulk_operations import named
from mcp_server_paperless.services.custom_values import linked
from mcp_server_paperless.services.document_edits import (
    MAX_TITLE,
    EditPlan,
    affected,
    implicit,
)
from mcp_server_paperless.services.document_runner import (
    MAX_WHY,
    RUN_SECONDS,
    WHY_CHARS,
    Ended,
    cut,
    one_by_one,
    reasons,
    refused,
    stopped_by,
)
from mcp_server_paperless.services.documents import document_link, known_items, readable
from mcp_server_paperless.services.selection import SELECT_BYTES
from mcp_server_paperless.services.taxonomy import fold
from mcp_server_paperless.services.taxonomy_edits import documents as count_documents

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.bulk_items import DocumentValues
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.domain.selection import DocumentRow, ReadField
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.services.document_edits import Created, DocumentEditor
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# How many documents a confirmation shows with their values (owner, 2026-11-24); all are bound.
SHOWN: Final = 20
# Characters of a value in an answer that would be too large otherwise.
MAX_VALUE: Final = 200
FIELDS: Final[tuple[ReadField, ...]] = (
    *("title", "created", "archive_serial_number", "owner", "correspondent", "document_type"),
    *("storage_path", "tags", "custom_fields", "user_can_change"),
)
LINKED_FIELDS: Final[tuple[ReadField, ...]] = ("title", "custom_fields", "user_can_change")
# The fields of a document in the order they are told, and the kind of the object they name.
SCALARS: Final[Mapping[str, TaxonomyKind | None]] = {
    "title": None,
    "created": None,
    "archive_serial_number": None,
    "correspondent": "correspondent",
    "document_type": "document_type",
    "storage_path": "storage_path",
    "owner": "user",
}
WORKFLOWS: Final = "Paperless runs its workflows for changed documents on each"
MEANWHILE: Final = "A value this call touches changed after it was checked, so nothing was written."
BY_NOW: Final = "By the time it was read again it was as asked."
NOT_YET: Final = (
    "Read again: it is not, or only partly (see changes), as asked, and may still become so."
)
UNLINKED: Final = "Links to it may be gone on document {ids}."
LINK_NEEDS: Final = "A document link needs change permission on the linked document."
AGAIN: Final = "Pass these items again once the reason is gone; what is done stays as it is."
GIVEN_AWAY: Final = (
    "Leave out document {ids}, which was or may have been given away: you may no longer see or "
    "change it."
)
TOO_SLOW: Final = (
    "Nothing was changed: checking the documents and their links took longer than "
    f"{RUN_SECONDS:g} seconds."
)
NOT_OWNER: Final = "Only its owner may change the owner."
# The largest archive serial number of Paperless.
MAX_NUMBER: Final = 2**32 - 1
GO_ON: Final = "Pass the items in left again; what is done stays as it is."
CORRECT: Final = "Correct these items or leave them out; the others were not changed."
# A new tag's name is shown whole (owner, 2026-11-24): the room ``quoted`` gives 768 characters holds
# the longest name with every character escaped.
NEW_TAG: Final = 6 * MAX_TITLE
# Characters of a refusal around what is wrong with an item, at most.
ITEM_CHARS: Final = 100
# What was sent to Paperless, or would be.
SENT: Final[frozenset[UpdateStatus]] = frozenset({"would_change", "changed", "unknown"})
# What is about one item and is named with it; anything else ends the call.
ABOUT_ITEM: Final = (Ambiguous, NotFound, PermissionDenied, Validation)


@dataclass(frozen=True, slots=True)
class _Item:
    """One document of the call, as it was read, and what is asked of it."""

    state: DocumentState
    plan: EditPlan

    @property
    def id(self) -> int:
        return self.state.id

    @property
    def fields(self) -> set[int]:
        """The custom fields the item names."""
        return self.plan.sets.keys() | self.plan.removed

    def touched(self, state: DocumentState, fields: Mapping[int, JsonValue]) -> object:
        """The values the item names, as ``state`` has them, its custom fields as ``fields``."""
        return (
            [(name, repr(_scalar(state, name))) for name in sorted(self.plan.scalars)],
            sorted(state.tags) if self.plan.tags.named else None,
            [(n, _comparable(fields[n]) if n in fields else None) for n in sorted(self.fields)],
        )


@dataclass(frozen=True, slots=True)
class _Change:
    """A field whose value differs, for the answer and for the confirmation."""

    value: ValueChange
    name: str
    text: str


@dataclass(slots=True)
class _Names:
    """What ids are called in messages."""

    known: dict[TaxonomyKind, dict[int, TaxonomyItem]]
    titles: dict[int, str]
    created: dict[int, str] = field(default_factory=dict[int, str])

    def of(self, kind: TaxonomyKind, number: int) -> str:
        item = self.known.get(kind, {}).get(number)
        return item.name if item else self.created.get(number, f"#{number}")

    def document(self, number: int) -> str:
        title = self.titles.get(number)
        return str(number) if title is None else f"{number} {quoted(title)}"


@dataclass(slots=True)
class _Call:
    """What phase 1 found."""

    items: list[_Item]
    names: _Names
    # The ids the tags to create have in patches until they exist; negative.
    placeholders: Created
    links: set[int]
    patches: dict[int, DocumentPatch]
    also: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    # The other documents each item changes by its links.
    reach: dict[int, set[int]] = field(default_factory=dict)
    # In phase 2: the tags created, and the custom fields each document is expected to have.
    created: Created = field(default_factory=dict[str, tuple[str, int]])
    fields: dict[int, dict[int, JsonValue]] = field(default_factory=dict)

    @property
    def changing(self) -> list[_Item]:
        return [item for item in self.items if self.patches[item.id].model_fields_set]

    @property
    def new_tags(self) -> list[str]:
        return [name for name, _ in self.placeholders.values()]

    @property
    def spellings(self) -> dict[str, list[str]]:
        """The new tags by name, and the other ways the items spell them."""
        found: dict[str, list[str]] = {name: [] for name in self.new_tags}
        for name in (n for item in self.items for n in item.plan.tags.new):
            first = self.placeholders[fold(name)][0]
            if name != first and name not in found[first]:
                found[first].append(name)
        return found

    @property
    def owners(self) -> list[_Item]:
        """The items that change an owner."""
        return [i for i in self.items if "owner" in self.patches[i.id].model_fields_set]


def _values(state: DocumentState) -> dict[int, JsonValue]:
    return {item.field: item.value for item in state.custom_fields}


def _comparable(value: JsonValue) -> str:
    return repr(sorted(map(repr, value)) if isinstance(value, list) else value)


def _applied(state: DocumentState, patch: DocumentPatch) -> DocumentState:
    """``state`` as ``patch`` makes it."""
    changed = {name: cast("object", getattr(patch, name)) for name in patch.model_fields_set}
    return state.model_copy(update=changed)


def _final(state: DocumentState, patch: DocumentPatch, links: set[int]) -> dict[int, set[int]]:
    """The targets of every link field ``patch`` sends (nothing if it sends no custom field)."""
    if patch.custom_fields is None:
        return {}
    sent = {v.field: v.value for v in patch.custom_fields}
    return {n: linked(sent.get(n)) - {state.id} for n in links}


def _shown(value: JsonValue, names: _Names, *, link: bool = False) -> str:
    """A value as a confirmation shows it: a text quoted and cut, links with their titles."""
    if value is None:
        return "none"
    if isinstance(value, str):
        return quoted(value)
    if link:
        return f"[{', '.join(names.document(n) for n in sorted(linked(value)))}]"
    return json.dumps(value)


class BulkUpdates:
    """Changes many documents, each with values of its own."""

    def __init__(  # noqa: PLR0913 -- what it reads, writes and measures time with
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        editor: DocumentEditor,
        taxonomy: TaxonomyResolver,
        *,
        web_url: str,
        clock: Callable[[], float],
    ) -> None:
        """Plan each change with ``editor``; ``clock`` bounds the call."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._editor: DocumentEditor = editor
        self._taxonomy: TaxonomyResolver = taxonomy
        self._web_url: str = web_url
        self._clock: Callable[[], float] = clock

    async def update(
        self,
        values: Sequence[DocumentValues],
        *,
        create_missing_tags: bool,
        dry_run: bool,
        confirm: bool,
    ) -> UpdateReport:
        """Preview (``dry_run``) or make the changes, each document with its own.

        Nothing is written for a preview, if an item is wrong, or without a confirmation.

        Raises:
            Validation: Too many documents, one named twice, or a wrong item; all are named.
            ConfirmationRequired: Documents would change and that is not confirmed.
            PaperlessMcpError: A tag could not be created, or no document is known to be changed
                and at least one is not as asked; the message names them.
        """
        until = self._clock() + RUN_SECONDS
        call = await self._check(
            values, create_missing_tags=create_missing_tags, until=None if dry_run else until
        )
        changing = call.changing
        if dry_run:
            return self._preview(call)
        if not changing:
            return self._report(call, {}, (), "")
        if not confirm:
            ConfirmationRequired.require(self._effect(call), scope=self._scope(call))
        call.created = await self._editor.create_tags(
            call.new_tags, go_on=lambda: self._clock() < until
        )
        call.names.created = {number: name for name, number in call.created.values()}
        call.fields = {item.id: _values(item.state) for item in call.items}
        rows, stop = await one_by_one(
            changing,
            lambda item: self._apply(item, call),
            until=until,
            clock=self._clock,
        )
        ended = {row.id: row for row in rows}
        return self._report(call, ended, tuple(name for name, _ in call.created.values()), stop)

    # --- phase 1

    async def _check(
        self, values: Sequence[DocumentValues], *, create_missing_tags: bool, until: float | None
    ) -> _Call:
        """What the call would do; refuses it as a whole if anything is wrong."""
        ids = [value.document_id for value in values]
        if not 1 <= len(ids) <= MAX_DOCUMENTS:
            message = f"Name between 1 and {MAX_DOCUMENTS} documents; got {len(ids)}."
            raise Validation(message, hint="Work in several calls.")
        twice = sorted({n for n in ids if ids.count(n) > 1})
        if twice:
            message = f"Nothing was changed: documents named twice: {', '.join(map(str, twice))}."
            raise Validation(message, hint="Put all values of a document into one item.")
        rows = await self._rows(ids, FIELDS, until)
        wrong: dict[int, list[str]] = {}
        items: list[_Item] = []
        for value in values:
            row = rows.get(value.document_id)
            if row is None:
                wrong[value.document_id] = ["It does not exist or is hidden from you."]
                continue
            state = DocumentState.model_validate(row)
            if state.user_can_change is False:
                wrong[state.id] = ["You may not change it."]
                continue
            edit = value.edit(create_missing_tags=create_missing_tags)
            try:
                items.append(_Item(state, await self._editor.plan(edit, state)))
            except ABOUT_ITEM as error:
                wrong[state.id] = [error.describe()]
        call = await self._call(items)
        for number, text in [
            *_number_conflicts(items),
            *await self._link_conflicts(call, until),
            *await self._owner_conflicts(call),
        ]:
            wrong.setdefault(number, []).append(text)
        if wrong:
            verb = "is" if len(wrong) == 1 else "are"
            message = (
                f"Nothing was changed: {len(wrong)} of {count_documents(len(ids))} {verb} "
                f"refused. {_all({n: ' '.join(wrong[n]) for n in ids if n in wrong})}"
            )
            raise Validation(message, hint=CORRECT)
        return call

    async def _rows(
        self, ids: Iterable[int], fields: Sequence[ReadField], until: float | None
    ) -> dict[int, DocumentRow]:
        wanted = list(ids)
        found: dict[int, DocumentRow] = {}
        for start in range(0, len(wanted), MAX_DOCUMENTS):
            self._require_time(until)
            part = wanted[start : start + MAX_DOCUMENTS]
            try:
                rows = await self._reader.read_rows(part, fields, max_bytes=SELECT_BYTES)
            except AnswerTooLarge:
                message = "The documents of this call are larger than 64 MiB."
                raise AnswerTooLarge(message, hint="Name fewer documents per call.") from None
            found.update((row.id, row) for row in rows)
        return found

    def _require_time(self, until: float | None) -> None:
        """Refuse what cannot be checked in time; nothing would be written after that anyway."""
        if until is not None and self._clock() >= until:
            raise PaperlessMcpError(TOO_SLOW, hint="Name fewer documents or links per call.")

    async def _known(self, items: Sequence[_Item]) -> dict[TaxonomyKind, dict[int, TaxonomyItem]]:
        """The objects of the kinds the items name."""
        kinds: set[TaxonomyKind] = {
            kind for item in items for name in item.plan.scalars if (kind := SCALARS[name])
        }
        if any(item.plan.tags.named for item in items):
            kinds.add("tag")
        if any(item.fields for item in items):
            kinds.add("custom_field")
        return {kind: await known_items(self._taxonomy, kind) for kind in kinds}

    async def _call(self, items: list[_Item]) -> _Call:
        placeholders: Created = {}
        for item in items:
            for name in item.plan.tags.new:
                _ = placeholders.setdefault(fold(name), (name, -1 - len(placeholders)))
        known = await self._known(items)
        return _Call(
            items=items,
            names=_Names(
                known,
                {item.id: item.state.title for item in items},
                {number: name for name, number in placeholders.values()},
            ),
            placeholders=placeholders,
            links={
                number
                for number, found in known.get("custom_field", {}).items()
                if found.data_type == "documentlink"
            },
            patches={item.id: item.plan.patch(item.state, placeholders) for item in items},
        )

    async def _link_conflicts(self, call: _Call, until: float | None) -> list[tuple[int, str]]:
        """What is wrong with document links, by document; also finds who else changes."""
        finals = {
            item.id: _final(item.state, call.patches[item.id], call.links) for item in call.items
        }
        targets = {n for final in finals.values() for ids in final.values() for n in ids}
        of_call = {item.id: item for item in call.items}
        rows = await self._rows(sorted(targets - of_call.keys()), LINKED_FIELDS, until)
        call.names.titles |= {n: row.title for n, row in rows.items() if row.title is not None}
        have = {n: _values(item.state) for n, item in of_call.items()} | {
            n: {v.field: v.value for v in row.custom_fields or ()} for n, row in rows.items()
        }
        unusable = {
            n for n in targets if n not in have or (n in rows and rows[n].user_can_change is False)
        }
        found: list[tuple[int, str]] = []
        call.reach = {item.id: set() for item in call.items}
        for item, number in itertools.product(call.items, sorted(call.links)):
            name = quoted(call.names.of("custom_field", number))
            final = finals[item.id].get(number, set())
            bad = ", ".join(map(str, sorted(final & unusable)))
            if bad:
                text = (
                    f"{name} links to document {bad}, which you cannot see or may not change; "
                    "Paperless would refuse it."
                )
                found.append((item.id, text))
            new = {n for n in final - unusable if item.id not in linked(have[n].get(number))}
            before = linked(_values(item.state).get(number)) - {item.id}
            dropped = before - final if finals[item.id] else set[int]()
            call.reach[item.id] |= {
                n for n in new | dropped if n not in of_call or number not in of_call[n].fields
            }
            kept = linked(item.plan.sets.get(number)) - {item.id}
            for other in sorted((kept | new) & of_call.keys()):
                found += _contradiction(item, of_call[other], number, name)
        call.also = await self._titled({n for ids in call.reach.values() for n in ids}, call, until)
        return found

    async def _owner_conflicts(self, call: _Call) -> list[tuple[int, str]]:
        """Owner changes Paperless would refuse, and links to what was given away before them."""
        if not call.owners:
            return []
        user = await self._reader.get_signed_in()
        if user is not None and user.is_superuser:
            return []
        found = [
            (item.id, NOT_OWNER)
            for item in call.owners
            if user is not None and item.state.owner not in {None, user.id}
        ]
        away = {
            item.id
            for item in call.owners
            if (owner := call.patches[item.id].owner) is not None
            and (user is None or owner != user.id)
        }
        late = _links_to_given_away(call, call.items, away)
        named = {item.id for item, _, _ in late}
        first = sorted(call.items, key=lambda item: item.id not in named)
        advice = (
            "make this change in an earlier call"
            if _links_to_given_away(call, first, away)
            else "put this item first"
        )
        for item, number, targets in late:
            name = quoted(call.names.of("custom_field", number))
            lost = ", ".join(map(str, sorted(targets)))
            text = (
                f"{name} links to document {lost}, which an earlier item of this call gives away; "
                f"{advice}."
            )
            found.append((item.id, text))
        return found

    async def _titled(
        self, numbers: set[int], call: _Call, until: float | None
    ) -> list[tuple[int, str]]:
        """Titles of the other documents a change reaches ('#id' if hidden)."""
        lacking = sorted(numbers - call.names.titles.keys())
        for start in range(0, len(lacking), MAX_DOCUMENTS):
            self._require_time(until)
            part = lacking[start : start + MAX_DOCUMENTS]
            call.names.titles |= await self._reader.get_document_titles(part)
        return [(n, call.names.titles.get(n, f"#{n}")) for n in sorted(numbers)]

    # --- what is shown

    def _changes(
        self, item: _Item, before: DocumentState, after: DocumentState, names: _Names
    ) -> list[_Change]:
        """The named fields that differ between two states of the document."""
        found: list[_Change] = []
        for name, kind in SCALARS.items():
            old, new = _scalar(before, name), _scalar(after, name)
            if name in item.plan.scalars and old != new:
                told = [
                    names.of(kind, v) if kind and isinstance(v, int) else _json(v)
                    for v in (old, new)
                ]
                label = name.replace("_", " ")
                text = f"{_shown(told[0], names)} -> {_shown(told[1], names)}"
                value = ValueChange(field=name, old=told[0], new=told[1])
                found.append(_Change(value, label, f"{label} {text}"))
        if item.plan.tags.named and set(before.tags) != set(after.tags):
            gained = [f"+{quoted(names.of('tag', t))}" for t in after.tags if t not in before.tags]
            lost = [f"-{quoted(names.of('tag', t))}" for t in before.tags if t not in after.tags]
            value = ValueChange(
                field="tags",
                old=[names.of("tag", t) for t in before.tags],
                new=[names.of("tag", t) for t in after.tags],
            )
            found.append(_Change(value, "tags", f"tags {', '.join([*gained, *lost])}"))
        return found + self._field_changes(item, _values(before), _values(after), names)

    @staticmethod
    def _field_changes(
        item: _Item, before: dict[int, JsonValue], after: dict[int, JsonValue], names: _Names
    ) -> list[_Change]:
        found: list[_Change] = []
        for number in sorted(item.fields):
            known = names.known.get("custom_field", {}).get(number)
            old, new = (readable(v.get(number), known) for v in (before, after))
            if (number in before) == (number in after) and _comparable(old) == _comparable(new):
                continue
            name = names.of("custom_field", number)
            link = known is not None and known.data_type == "documentlink"
            label = quoted(name)
            text = f"{_shown(old, names, link=link)} -> {_shown(new, names, link=link)}"
            if number not in after:
                text = "removed"
            value = ValueChange(field=f"custom_fields.{name}", old=old, new=new)
            found.append(_Change(value, label, f"{label} {text}"))
        return found

    def _planned(self, item: _Item, call: _Call) -> list[_Change]:
        after = _applied(item.state, call.patches[item.id])
        return self._changes(item, item.state, after, call.names)

    def _effect(self, call: _Call) -> str:
        """What confirming would do: every document, and of the first ``SHOWN`` the values."""
        told: list[str] = []
        for at, item in enumerate(call.changing):
            changes = self._planned(item, call)
            what = "; ".join(c.text for c in changes) if at < SHOWN else _joined(changes)
            told.append(f"{item.id} {quoted(item.state.title)} ({what})")
        parts: list[str] = []
        if call.owners:
            handed = " and of ".join(
                f"document {i.id} {quoted(i.state.title)} to {self._owner(i, call)}"
                for i in call.owners
            )
            lose = "a new owner takes it over and you may lose access to it"
            parts.append(f"change the owner of {handed}: {lose}")
        if call.new_tags:
            noun = "tag" if len(call.new_tags) == 1 else "tags"
            listed = ", ".join(_spelled(name, also) for name, also in call.spellings.items())
            parts.append(f"create the {noun} {listed}, private to you until shared")
        parts.append(f"change {count_documents(len(told))}: {', '.join(told)}")
        if call.also:
            others = named(call.also, " besides")
            parts.append(f"and, because document links are mirrored, also change {others}")
        return f"{'; '.join(parts)}. {WORKFLOWS}"

    @staticmethod
    def _owner(item: _Item, call: _Call) -> str:
        owner = call.patches[item.id].owner
        if owner is None:
            return "no owner (every user who may view documents sees it)"
        known = call.names.known["user"].get(owner)
        return f"user {owner}" if known is None else f"{quoted(known.name)} (id {owner})"

    @staticmethod
    def _scope(call: _Call) -> object:
        """What the text does not tell apart: all values, the old ones and the new."""
        touched = [
            (
                i.id,
                i.touched(i.state, _values(i.state)),
                sorted(call.patches[i.id].model_dump(exclude_unset=True).items()),
            )
            for i in call.changing
        ]
        return touched, call.spellings, call.also

    def _preview(self, call: _Call) -> UpdateReport:
        rows = {
            item.id: self._row(
                item,
                "would_change",
                changes=tuple(c.value for c in self._planned(item, call)),
            )
            for item in call.changing
        }
        what = f"Confirming would {self._effect(call)}. {TO_GO_AHEAD}" if rows else _nothing(call)
        return self._built(call, rows, tuple(call.new_tags), f"{DRY_RUN} {what}", dry_run=True)

    # --- phase 2

    async def _apply(self, item: _Item, call: _Call) -> Ended[UpdatedDocument]:
        """Read the document again and write it if it still is as it was checked."""
        try:
            now = DocumentState.model_validate(await self._reader.get_document(item.id))
        except PaperlessMcpError as error:
            row = self._row(item, "failed", error.message)
            return Ended(row, stopped_by(error), silent=not refused(error))
        patch = item.plan.patch(now, call.created)
        if not patch.model_fields_set:
            return Ended(self._row(item, "unchanged", BY_NOW))
        if item.touched(now, _values(now)) != item.touched(item.state, call.fields[item.id]):
            return Ended(self._row(item, "changed_meanwhile", MEANWHILE))
        try:
            answer = await self._writer.update_document(item.id, patch)
        except PaperlessMcpError as error:
            if refused(error):
                why = _refusal(error, item, now, patch, call.links)
                return Ended(self._row(item, "failed", why), stopped_by(error))
            return await self._look_up(item, call, now, patch, error)
        finally:
            for kind in affected(patch):
                self._taxonomy.invalidate(kind)
        after = DocumentState.model_validate(answer)
        return Ended(self._changed(item, call, now, after, patch))

    async def _look_up(
        self,
        item: _Item,
        call: _Call,
        before: DocumentState,
        patch: DocumentPatch,
        error: PaperlessMcpError,
    ) -> Ended[UpdatedDocument]:
        """What became of a change whose answer was lost, read once."""
        try:
            after = DocumentState.model_validate(await self._reader.get_document(item.id))
        except PaperlessMcpError as second:
            why = f"{error.message} It could not be read again: {second.message}"
            return Ended(self._row(item, "unknown", why), stopped_by(second), silent=True)
        if item.plan.patch(after, call.created).model_fields_set:
            why = f"{error.message} {NOT_YET}"
            seen = tuple(c.value for c in self._changes(item, before, after, call.names))
            return Ended(self._row(item, "unknown", why, changes=seen), silent=True)
        row = self._changed(item, call, before, after, patch)
        why = f"{error.message} Read again: it is as asked."
        return Ended(row.model_copy(update={"why": why}), silent=True)

    def _changed(
        self,
        item: _Item,
        call: _Call,
        before: DocumentState,
        after: DocumentState,
        patch: DocumentPatch,
    ) -> UpdatedDocument:
        _mirror(before, patch, call.links, call.fields)
        along = implicit(before, after, item.plan, call.created) if patch.tags is not None else []
        return self._row(
            item,
            "changed",
            changes=tuple(c.value for c in self._changes(item, before, after, call.names)),
            implicit_tags=tuple(Ref(id=t, name=call.names.of("tag", t)) for t in along),
        )

    # --- the answer

    def _row(
        self,
        item: _Item,
        status: UpdateStatus,
        why: str | None = None,
        *,
        changes: tuple[ValueChange, ...] = (),
        implicit_tags: tuple[Ref, ...] = (),
    ) -> UpdatedDocument:
        return UpdatedDocument(
            id=item.id,
            title=item.state.title,
            status=status,
            why=why,
            changes=changes,
            implicit_tags=implicit_tags,
            web_url=document_link(self._web_url, item.id),
        )

    def _report(
        self,
        call: _Call,
        ended: dict[int, UpdatedDocument],
        created: tuple[str, ...],
        stop: str,
    ) -> UpdateReport:
        rows = {
            item.id: ended.get(item.id) or self._row(item, "not_started") for item in call.changing
        }
        report = self._built(call, rows, created, "")
        changed = report.counts.get("changed", 0)
        bad = [row for row in report.documents if row.id in report.left]
        if not bad:
            message = f"Changed {count_documents(changed)}." if changed else _nothing(call)
            return report.model_copy(update={"message": message})
        counts = ", ".join(
            f"{n} {s}" for s, n in report.counts.items() if s not in {"changed", "unchanged"}
        )
        verb = "is" if len(bad) == 1 else "are"
        lacking = f"{len(bad)} of {count_documents(len(call.items))} {verb} not as asked: {counts}."
        why = [(row.id, row.status, row.why) for row in bad if row.why]
        # An error is all there is; an answer has the reasons with the documents.
        told = reasons(why, limit=MAX_WHY if changed else len(why))
        details = [f"Documents not as asked: {told}"] if why else []
        if stop:
            details.append(f"Stopped: {stop}")
        if created:
            listed = ", ".join(quoted(name) for name in created)
            details.append(f"Tags created: {listed}.")
        given = {item.id for item in call.owners}
        gone = [row.id for row in report.documents if row.id in given and row.status in SENT]
        away = [GIVEN_AWAY.format(ids=", ".join(map(str, gone)))] if gone else []
        if not changed and why and not any(row.changes for row in bad):
            message = f"No document is known to be changed; {' '.join([lacking, *details, *away])}"
            raise PaperlessMcpError(message, hint=READ_AGAIN)
        done = f"Changed {count_documents(changed)}."
        last = AGAIN if why else GO_ON
        message = " ".join([lacking, done, *details, last, *away])
        return _bounded(report.model_copy(update={"message": message}))

    def _built(
        self,
        call: _Call,
        rows: dict[int, UpdatedDocument],
        created: tuple[str, ...],
        message: str,
        *,
        dry_run: bool = False,
    ) -> UpdateReport:
        documents = tuple(rows.get(item.id) or self._row(item, "unchanged") for item in call.items)
        sent = {row.id for row in documents if row.status in SENT}
        reached = {n for number in sent for n in call.reach[number]}
        counts: dict[UpdateStatus, int] = {}
        for row in documents:
            counts[row.status] = counts.get(row.status, 0) + 1
        report = UpdateReport(
            dry_run=dry_run,
            counts=counts,
            documents=documents,
            also_changed=tuple(
                Ref(id=n, name=call.names.titles.get(n)) for n, _ in call.also if n in reached
            ),
            created_tags=created,
            left=tuple(row.id for row in documents if row.status not in {"changed", "unchanged"}),
            message=message,
        )
        return _bounded(report)


def _scalar(state: DocumentState, name: str) -> object:
    return cast("object", getattr(state, name))


def _json(value: object) -> JsonValue:
    """A plain field as JSON; dates in ISO form."""
    return value.isoformat() if isinstance(value, date) else cast("JsonValue", value)


def _all(wrong: Mapping[int, str]) -> str:
    """Every wrong item with what is wrong, in full unless that is larger than an answer may be."""
    if sum(len(text) + ITEM_CHARS for text in wrong.values()) <= MAX_ANSWER - ITEM_CHARS:
        room = MAX_ANSWER
    else:
        room = MAX_ANSWER // len(wrong) - 2 * ITEM_CHARS
    told = {
        n: text if len(text) <= room else f"{text[:room]}… ({len(text) - room} more characters)"
        for n, text in wrong.items()
    }
    return "; ".join(f"document {n}: {text}" for n, text in told.items())


def _spelled(name: str, also: Sequence[str]) -> str:
    more = f" (also for {', '.join(quoted(n, NEW_TAG) for n in also)})" if also else ""
    return f"{quoted(name, NEW_TAG)}{more}"


def _joined(changes: Sequence[_Change]) -> str:
    return ", ".join(change.name for change in changes)


def _nothing(call: _Call) -> str:
    if len(call.items) == 1:
        return "Nothing to change: the document already is as asked."
    return f"Nothing to change: all {len(call.items)} documents already are as asked."


def _number_conflicts(items: Sequence[_Item]) -> list[tuple[int, str]]:
    """Archive serial numbers that cannot be set in this order, by document."""
    field_name = "archive_serial_number"
    found: list[tuple[int, str]] = []
    wanted = [n for i in items if (n := i.plan.scalars.get(field_name)) is not None]
    holders = {i.id: i.state.archive_serial_number for i in items}
    for item in items:
        if field_name not in item.plan.scalars:
            continue
        number = cast("int | None", item.plan.scalars[field_name])
        if wanted.count(number) > 1:
            text = f"Archive serial number {number} is given to several documents of this call."
            found.append((item.id, text))
        elif number is not None and number > MAX_NUMBER:
            found.append((item.id, f"An archive serial number is at most {MAX_NUMBER}."))
        elif number is not None:
            for other, held in holders.items():
                if held == number and other != item.id:
                    text = (
                        f"Document {other} of this call has archive serial number {number} at "
                        "that point; release it there first."
                    )
                    found.append((item.id, text))
        holders[item.id] = number
    return found


def _links_to_given_away(
    call: _Call, order: Sequence[_Item], away: set[int]
) -> list[tuple[_Item, int, set[int]]]:
    """The link fields an item would send with a document in ``away`` that was changed before it.

    Links are mirrored as the items are made in this ``order``, so what an earlier item already
    linked back is not sent again, unless another custom field of the document is.
    """
    found: list[tuple[_Item, int, set[int]]] = []
    fields = {item.id: _values(item.state) for item in order}
    gone: set[int] = set()
    for item in order:
        now = item.state.model_copy(
            update={
                "custom_fields": tuple(
                    CustomFieldValue(field=n, value=v) for n, v in fields[item.id].items()
                )
            }
        )
        patch = item.plan.patch(now, call.placeholders)
        for number, targets in sorted(_final(now, patch, call.links).items()):
            if targets & gone:
                found.append((item, number, targets & gone))
        _mirror(now, patch, call.links, fields)
        gone |= away & {item.id}
    return found


def _contradiction(item: _Item, other: _Item, number: int, name: str) -> list[tuple[int, str]]:
    """Both items, if one links to a document whose own item sets that field without the link."""
    if number not in other.fields or item.id in linked(other.plan.sets.get(number)):
        return []
    links = (
        f"It links to document {other.id} in {name}, but the item of {other.id} sets that field "
        f"without {item.id}."
    )
    sets = f"It sets {name} without document {item.id}, whose item links to it."
    return [(item.id, links), (other.id, sets)]


def _refusal(
    error: PaperlessMcpError,
    item: _Item,
    before: DocumentState,
    patch: DocumentPatch,
    links: set[int],
) -> str:
    """Why Paperless refused a change, and what it may have done before it did."""
    why = error.message
    if not isinstance(error, PermissionDenied):
        return why
    if links & item.plan.sets.keys():
        why += f" {LINK_NEEDS}"
    final, have = _final(before, patch, links), _values(before)
    dropped = {
        n for number, targets in final.items() for n in linked(have.get(number)) - targets
    } - {item.id}
    if "owner" in patch.model_fields_set and dropped:
        why += f" {UNLINKED.format(ids=', '.join(map(str, sorted(dropped))))}"
    return why


def _mirror(
    before: DocumentState,
    patch: DocumentPatch,
    links: set[int],
    fields: dict[int, dict[int, JsonValue]],
) -> None:
    """Note in ``fields`` (of the documents of the call) what Paperless mirrored for a change."""
    have = _values(before)
    for number, final in _final(before, patch, links).items():
        for other in (linked(have.get(number)) | final) & fields.keys() - {before.id}:
            stored = fields[other].get(number)
            ids = [n for n in sorted(linked(stored)) if n != before.id]
            if other in final:
                fields[other][number] = [*ids, before.id]
            elif before.id in linked(stored):
                fields[other][number] = [*ids]


def _short(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_short(v) for v in value]
    return cut(value, MAX_VALUE) if isinstance(value, str) else value


def _bounded(report: UpdateReport) -> UpdateReport:
    """``report`` with short values, then without changes from the end, until it fits.

    A message that is too long even then is cut.
    """
    if len(report.model_dump_json()) <= MAX_ANSWER:
        return report
    rows = [
        row.model_copy(
            update={
                "why": None if row.why is None else cut(row.why),
                "changes": tuple(
                    ValueChange(field=c.field, old=_short(c.old), new=_short(c.new))
                    for c in row.changes
                ),
            }
        )
        for row in report.documents
    ]
    room = MAX_ANSWER
    report = report.model_copy(update={"documents": tuple(rows), "truncated": True})
    room -= len(report.model_dump_json())
    for at in reversed(range(len(rows))):
        if room >= 0:
            return report.model_copy(update={"documents": tuple(rows)})
        bare = rows[at].model_copy(update={"changes": ()})
        room += len(rows[at].model_dump_json()) - len(bare.model_dump_json())
        rows[at] = bare
    message = cut(report.message, max(WHY_CHARS, len(report.message) + min(room, 0)))
    return report.model_copy(update={"documents": tuple(rows), "message": message})
