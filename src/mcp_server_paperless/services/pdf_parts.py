"""The new documents of a page operation: found, verified, and given metadata (decision-67).

Names are resolved before anything starts. Afterwards every new document is looked at for itself:
metadata is set only on one that is known to be this call's, was waited for, succeeded, and has the
pages it should have. What is unfinished, hidden or in doubt is not touched; ``next`` names the
calls that finish it. Nothing is remembered between calls.

Paperless 3.5 lists no task for a file whose copied metadata holds a date among the custom fields.
Such documents are found by their file names (``PdfWriter.find_copies``), and only where nothing
else can be taken for them. Such a rotation is told by the versions it made
(``PdfWriter.find_rotations``, decision-74).
"""

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import DocumentEdit
from mcp_server_paperless.domain.errors import PaperlessMcpError, Validation, quoted
from mcp_server_paperless.domain.pdf import PartOutcome, PdfTask
from mcp_server_paperless.services.document_edits import check_edit
from mcp_server_paperless.services.documents import document_link
from mcp_server_paperless.services.page_ranges import describe, specification
from mcp_server_paperless.services.pdf_replacing import RUNNING, KeptError, cause, named, outputs
from mcp_server_paperless.services.tag_hierarchy import close_over_ancestors
from mcp_server_paperless.services.tasks import POLL_SECONDS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord, DocumentUpdate
    from mcp_server_paperless.domain.pdf import (
        FoundCopies,
        PartMetadata,
        PdfResult,
        StartedTasks,
    )
    from mcp_server_paperless.domain.ports import DocumentReader, PdfWriter
    from mcp_server_paperless.services.document_edits import DocumentEditor
    from mcp_server_paperless.services.pdf_replacing import Replacing
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

NEW: Final = "<the new document>"
NOT_SET: Final = "its metadata is not known to be set ({cause})"
UNCHECKED: Final = "it could not be checked ({cause})"
CHANGED: Final = "Paperless answered with other values than asked for"
TOO_LATE: Final = "the call took too long to set its metadata"
DOUBTED: Final = "it cannot be told that it is this part ({doubt})"
NOT_YET: Final = "there is no document with the file name {name} yet"
NOT_TURNED: Final = "There is no new version with the file name {name} yet."
TURNED: Final = (
    "Paperless lists no task for the rotation of {documents}, because a custom field holds a date: "
    "{found} of {expected} new {versions} found and told by file name, time and user."
)
UNTOLD: Final = "Which new version is the rotation could not be told ({doubt})."
NO_DATE: Final = "document {number} does not hold the date of the custom field that is copied"
UNREAD: Final = "document {number} could not be read"


@dataclass(frozen=True, slots=True)
class Part:
    """One new document as it was asked for.

    Attributes:
        label: Its pages in words.
        edit: Its metadata, with ids, None where it takes what Paperless copies.
        words: The metadata in words, for plan and confirmation.
    """

    label: str
    edit: DocumentEdit | None = None
    words: str = ""


@dataclass(frozen=True, slots=True)
class Found:
    """The documents or versions found by their file names, as tasks without an id.

    Attributes:
        note: What the result says of the search, if it is not the usual sentence.
    """

    tasks: tuple[PdfTask, ...]
    waited: float
    note: str = ""


def part_lines(parts: Sequence[Part], *, single: bool) -> tuple[str, ...]:
    """What the parts get, for the plan and the confirmation."""
    return tuple(
        f"{'the new document' if single else f'part {n} ({part.label})'} gets {part.words}"
        for n, part in enumerate(parts, start=1)
        if part.edit is not None
    )


def _label(pages: Sequence[int]) -> str:
    """The pages in words, and their order where it is not the natural one."""
    if list(pages) == sorted(pages):
        return describe(pages)
    return f"pages {specification(pages)} (in this order)"


def unset_reason(outcomes: Sequence[PartOutcome], *, single: bool) -> str | None:
    """What metadata is not set, if any."""
    missing = [str(outcome.part) for outcome in outcomes if outcome.metadata == "not_set"]
    if not missing:
        return None
    if single:
        return "the metadata of the new document is not set"
    noun = "part" if len(missing) == 1 else "parts"
    return f"the metadata of {noun} {', '.join(missing)} is not set"


def _update_call(edit: DocumentEdit, target: int | str) -> str:
    """The update_document call that sets the metadata."""
    given = edit.model_dump(
        mode="json",
        include={"title", "document_type", "correspondent", "created", "tags_set"},
        exclude_none=True,
    )
    values = [f"{name}={json.dumps(given[name], ensure_ascii=False)}" for name in given]
    return f"update_document({', '.join([f'document_id={target}', *values])})"


def _holds(update: DocumentUpdate, edit: DocumentEdit, parents: Mapping[int, int | None]) -> bool:
    """Whether the answer to an update has what was asked for, and the parents of its tags."""
    kind, writer = update.document_type, update.correspondent
    return (
        edit.title in {None, update.title}
        and edit.created in {None, update.created}
        and edit.document_type in {None, kind and kind.id}
        and edit.correspondent in {None, writer and writer.id}
        and (
            edit.tags_set is None
            or {*close_over_ancestors(map(int, edit.tags_set), parents)}
            == {tag.id for tag in update.tags}
        )
    )


class Parts:
    """Resolves the metadata of new documents, finds and verifies them, and sets it."""

    def __init__(  # noqa: PLR0913 -- one port or service per concern
        self,
        reader: DocumentReader,
        writer: PdfWriter,
        editor: DocumentEditor,
        taxonomy: TaxonomyResolver,
        replacing: Replacing,
        *,
        web_url: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Verify with ``replacing``, whose clock bounds the steps; set through ``editor``."""
        self._reader: DocumentReader = reader
        self._writer: PdfWriter = writer
        self._editor: DocumentEditor = editor
        self._taxonomy: TaxonomyResolver = taxonomy
        self._replacing: Replacing = replacing
        self._web_url: str = web_url
        self._sleep: Callable[[float], Awaitable[None]] = sleep

    async def resolve(
        self, pages: Sequence[Sequence[int]], metadata: Sequence[PartMetadata] | None
    ) -> tuple[Part, ...]:
        """The parts of these pages with their metadata, names resolved.

        Raises:
            Validation: Not one entry per part, or a bad title.
            NotFound: A name matches nothing.
            Ambiguous: A name matches more than one object.
        """
        if metadata is None:
            return tuple(Part(_label(numbers)) for numbers in pages)
        if len(metadata) != len(pages):
            message = f"There are {len(pages)} parts, but metadata for {len(metadata)}."
            raise Validation(
                message, hint="Give one entry per part, in their order; {} takes what is copied."
            )
        return tuple(
            [
                await self._part(_label(numbers), wanted)
                for numbers, wanted in zip(pages, metadata, strict=True)
            ]
        )

    async def _part(self, label: str, wanted: PartMetadata) -> Part:
        if wanted == type(wanted)():
            return Part(label)
        words: list[str] = []
        ids: dict[str, int] = {}
        if wanted.title is not None:
            words.append(f"title {quoted(wanted.title.strip())}")
        for kind in ("document_type", "correspondent"):
            value = wanted.document_type if kind == "document_type" else wanted.correspondent
            if value is not None:
                item = await self._taxonomy.resolve(kind, value)
                ids[kind] = item.id
                words.append(f"{kind.replace('_', ' ')} {quoted(item.name)}")
        if wanted.created is not None:
            words.append(f"created {wanted.created}")
        tags = None
        if wanted.tags is not None:
            tags = [await self._taxonomy.resolve("tag", value) for value in wanted.tags]
            names = ", ".join(quoted(tag.name) for tag in tags)
            words.append(f"tags {names}" if tags else "no tags")
        edit = DocumentEdit(
            title=None if wanted.title is None else wanted.title.strip(),
            created=wanted.created,
            tags_set=None if tags is None else tuple(tag.id for tag in tags),
            document_type=ids.get("document_type"),
            correspondent=ids.get("correspondent"),
        )
        check_edit(edit)
        return Part(
            label, edit, " and ".join([*([", ".join(words[:-1])] * bool(words[:-1])), words[-1]])
        )

    async def look(
        self, started: StartedTasks, dates: Mapping[int, object] | None, bound: float
    ) -> tuple[StartedTasks, Found | None]:
        """Find what a request made whose tasks a date hides, waiting for up to ``bound`` seconds.

        ``dates`` are the dates among the custom fields that were copied, None if not known.
        Returns the request with what is now known of its files, and the documents, if any.
        """
        files = started.files
        if started.ids or started.problem or files is None or not dates or bound <= 0:
            return started, None
        searched = await self._search(
            lambda final: self._writer.find_copies(files, final=final), bound
        )
        if searched is None:
            return started, None
        copies, waited = searched
        doubt = copies.doubt or await self._dateless(copies.ids, dates)
        if doubt is not None:
            return started.model_copy(update={"doubt": doubt}), None
        if not any(copies.ids):
            return started, None
        known = {"outputs": tuple(range(len(files.names))), "included": files.included}
        tasks = tuple(
            _task(number, name) for number, name in zip(copies.ids, files.names, strict=True)
        )
        return started.model_copy(update=known), Found(tasks, waited)

    async def turned(
        self, started: StartedTasks, before: Mapping[int, int], bound: float
    ) -> tuple[StartedTasks, Found | None]:
        """Find the versions of a rotation whose tasks a date hides, for up to ``bound`` seconds.

        ``before`` is the version each such document showed before the request. Returns the request
        with the documents of what is found, which comes before its listed tasks.
        """
        files = started.files
        if started.problem or files is None or not before or bound <= 0:
            return started, None
        searched = await self._search(
            lambda final: self._writer.find_rotations(files.request, before, final=final), bound
        )
        if searched is None:
            return started, None
        copies, waited = searched
        if copies.doubt is not None:
            return started, Found((), waited, UNTOLD.format(doubt=copies.doubt))
        tasks = tuple(
            _task(version, f"{number}_rotated.pdf", NOT_TURNED)
            for number, version in zip(before, copies.ids, strict=True)
        )
        note = TURNED.format(
            documents=named(list(before)),
            found=sum(n is not None for n in copies.ids),
            expected=len(before),
            versions="version" if len(before) == 1 else "versions",
        )
        known = started.model_copy(update={"sources": (*before, *started.sources)})
        return known, Found(tasks, waited, note)

    async def _search(
        self, find: Callable[[bool], Awaitable[FoundCopies]], bound: float
    ) -> tuple[FoundCopies, float] | None:
        """What is found within ``bound`` seconds and how long that took; None if it broke."""
        begun = self._replacing.clock()
        while True:
            left = bound - (self._replacing.clock() - begun)
            try:
                copies = await find(left <= 0)
            except PaperlessMcpError:
                return None
            settled = copies.doubt is None and None not in copies.ids
            if settled or (copies.doubt is not None and not copies.passing) or left <= 0:
                return copies, min(self._replacing.clock() - begun, bound)
            await self._sleep(min(POLL_SECONDS, left))

    async def _dateless(self, ids: Sequence[int | None], dates: Mapping[int, object]) -> str | None:
        """Which found document lacks the date that hid its task, so it may be another's."""
        for number in ids:
            if number is None:
                continue
            try:
                document = await self._reader.get_document(number)
            except PaperlessMcpError:
                return UNREAD.format(number=number)
            held = {value.field: value.value for value in document.custom_fields}
            if any(held.get(field) != date for field, date in dates.items()):
                return NO_DATE.format(number=number)
        return None

    async def settle(  # noqa: PLR0913 -- what was asked for and what became of it
        self,
        result: PdfResult,
        started: StartedTasks,
        parts: Sequence[Part],
        pages: Mapping[int, int | None],
        source: DocumentRecord,
        *,
        began: float,
    ) -> tuple[PartOutcome, ...]:
        """What became of every part; metadata is set where the part is verified."""
        tasks: dict[int, PdfTask] = {}
        if started.problem != "unconfirmed":
            tasks = dict(zip(outputs(result, started), result.tasks, strict=True))
        taken, outcomes = {source.id}, list[PartOutcome]()
        for number, part in enumerate(parts):
            outcome = PartOutcome(
                part=number + 1,
                pages=part.label,
                task_id=None,
                state="unknown",
                document_id=None,
                web_url=None,
                metadata="not_requested" if part.edit is None else "not_set",
                message=None,
                next=None,
            )
            task, search = tasks.get(number), _search(part, source)
            if task is not None:
                outcome = outcome.model_copy(update={"task_id": task.task_id, "state": task.state})
            if task is None or started.problem is not None or started.doubt is not None:
                known = _untold(started, search)
            elif task.state in RUNNING or result.wait == "not_requested":
                known = self._unchecked(task, part, pages[number], search)
            else:
                known = {"message": task.message}
                if task.state == "success":
                    known = await self._finish(task, part, pages[number], taken, began)
            outcomes.append(outcome.model_copy(update=known))
        return tuple(outcomes)

    def _unchecked(
        self, task: PdfTask, part: Part, pages: int | None, search: str
    ) -> dict[str, object]:
        """What is known of a part that was not seen to its end."""
        known: dict[str, object] = {
            "message": task.message,
            "next": _await(task, part, pages, search),
        }
        if len(task.document_ids) == 1:
            known |= self._names(task.document_ids[0])
            if part.edit is None:
                known["next"] = None
        return known

    def _names(self, number: int) -> dict[str, object]:
        return {"document_id": number, "web_url": document_link(self._web_url, number)}

    async def _finish(
        self, task: PdfTask, part: Part, pages: int | None, taken: set[int], began: float
    ) -> dict[str, object]:
        """What is known of a part that is done; its metadata is set if it is what it should be."""
        number = task.document_ids[0] if len(task.document_ids) == 1 else None
        names = {} if number is None else self._names(number)
        try:
            document = await self._replacing.verified(task, pages, taken)
        except KeptError as kept:
            then = kept.next_call
            if then is not None and number is not None:
                then = _checked_by_hand(part, number, pages)
            return {**names, "message": kept.reason, "next": then}
        except Exception as error:  # noqa: BLE001 -- the documents exist: the other parts go on
            then = None if number is None else _checked_by_hand(part, number, pages)
            return {**names, "message": UNCHECKED.format(cause=cause(error)), "next": then}
        if part.edit is None:
            return names
        message: str | None = TOO_LATE
        if not self._replacing.late(began):
            try:
                update = await self._editor.update(document.id, part.edit)
                tags = part.edit.tags_set
                items = await self._taxonomy.items("tag") if tags is not None else []
                parents = {tag.id: tag.parent for tag in items}
                message = None if _holds(update, part.edit, parents) else CHANGED
            except Exception as error:  # noqa: BLE001 -- the document exists: say so
                message = NOT_SET.format(cause=cause(error))
        if message is None:
            return {**names, "metadata": "set"}
        return {**names, "message": message, "next": _update_call(part.edit, document.id)}


def _checked_by_hand(part: Part, number: int, pages: int | None) -> str:
    """The calls that finish a part whose document could not be read."""
    look = f"get_document(document_id={number})"
    if part.edit is None:
        return look
    size = f"{pages} {'page' if pages == 1 else 'pages'}"
    return f"{look}, only if it has {size}: {_update_call(part.edit, number)}"


def _untold(started: StartedTasks, search: str) -> dict[str, object]:
    """What is known of a part that cannot be told from other files: how to find it."""
    waits = started.ids[:1] if started.problem == "unconfirmed" else ()
    steps = [*(f'get_task(task_id="{n}", wait_seconds=30)' for n in waits), search]
    doubt = started.doubt
    message = None if doubt is None else DOUBTED.format(doubt=doubt)
    return {"message": message, "next": ", then ".join(steps)}


def _task(number: int | None, name: str, not_yet: str = NOT_YET) -> PdfTask:
    """A document or version found by its file name, or not yet, in place of a task."""
    if number is None:
        message = not_yet.format(name=name)
        return PdfTask(task_id=None, state="unknown", message=message, document_ids=())
    return PdfTask(task_id=None, state="success", message=None, document_ids=(number,))


def _search(part: Part, source: DocumentRecord) -> str:
    """How to find a part that cannot be followed."""
    find = (
        f"find the new document of {part.label} of document {source.id} (search_documents, newest "
        "added first) and check it"
    )
    return find if part.edit is None else f"{find}, then {_update_call(part.edit, NEW)}"


def _await(task: PdfTask, part: Part, pages: int | None, search: str) -> str:
    """The calls that finish a part that is not done."""
    if task.task_id is None:
        return search
    wait = f'get_task(task_id="{task.task_id}", wait_seconds=30)'
    if part.edit is None:
        return wait
    size = "page" if pages == 1 else "pages"
    return (
        f"{wait}; only if it succeeded and made exactly one document with {pages} {size} "
        f"(get_document): {_update_call(part.edit, NEW)}"
    )
