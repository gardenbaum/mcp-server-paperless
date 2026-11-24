"""PdfWriter on the PDF endpoints of Paperless 3.x (API version 10).

``POST /api/documents/{rotate,merge,edit_pdf,remove_password,reprocess}/`` answer
``{"result": "OK", "task_id": ...}`` once a ``bulk_update`` task is queued; it makes the files,
and they are consumed by tasks of their own, which appear in ``GET /api/tasks/`` within
milliseconds:

- ``consume_file`` per produced file, named by ``input_data.filename``: ``{id}_rotated.pdf``,
  ``{id}_edit_{n}.pdf`` (new documents) or ``{id}_edited.pdf`` (a new version),
  ``{id}_{id}_merged.pdf``, ``{id}_unprotected.pdf``;
- ``reprocess_document`` per document;

Paperless's own deletion of originals is never asked for: it hangs on the success of those tasks,
and a task also succeeds when it stops without making a document (decision-60).

So the writer notes the newest task before the request and afterwards looks for newer tasks of
the operation's kind, for a short bounded time. A user who may not view tasks gets none back.

The ``bulk_update`` task fails for what would be a 400 otherwise (a file that is not a PDF, a wrong
password). The writer waits for it (``queued.py``), turns a failure into an error, and only then
looks for the file tasks, with a fresh window.
"""

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import takewhile
from typing import TYPE_CHECKING, Final

from pydantic import AwareDatetime, BaseModel, JsonValue, RootModel

from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.pdf import CopiedFiles, FoundCopies, RotationSearch, StartedTasks
from mcp_server_paperless.domain.tasks import Unconfirmed
from mcp_server_paperless.paperless.errors import REJECTED
from mcp_server_paperless.paperless.pagination import Page
from mcp_server_paperless.paperless.queued import Queued, TaskWaiter, not_applied, task_failed

if TYPE_CHECKING:
    from collections.abc import (
        Awaitable,
        Callable,
        Collection,
        Iterable,
        Iterator,
        Sequence,
        Sized,
    )

    from mcp_server_paperless.domain.pdf import FileOutput, LookupProblem, PageSlot
    from mcp_server_paperless.paperless.client import PaperlessClient

logger = logging.getLogger(__name__)

BASE = "/api/documents/"
TASKS = "/api/tasks/"
SETTINGS = "/api/ui_settings/"
# How long the tasks of a request may take to show up, and how often the list is read meanwhile.
SETTLE_SECONDS: Final = 3.0
POLL_SECONDS: Final = 0.1
# Most tasks one read of the list looks at (an operation starts at most MAX_DOCUMENTS of them).
LOOKBACK: Final = 100

REFUSED = "The Paperless user may not change these documents."
REFUSED_HINT = (
    "Rotating, splitting, deleting pages, editing and removing a password need the permission to "
    "change documents and ownership of every document (or no owner): a change grant is not "
    "enough. Merging and reprocessing accept a change grant. A new document needs the permission "
    "to add documents."
)


class _Task(BaseModel):
    id: int
    task_id: str
    task_type: str
    date_created: str
    date_started: AwareDatetime | None = None
    date_done: AwareDatetime | None = None
    owner: int | None = None
    input_data: JsonValue = None

    @property
    def filename(self) -> str:
        data = self.input_data
        name = data.get("filename") if isinstance(data, dict) else None
        return name if isinstance(name, str) else ""

    @property
    def created(self) -> datetime:
        return datetime.fromisoformat(self.date_created)

    @property
    def operation(self) -> str | None:
        """What a queued task does, as far as it says."""
        named = self.input_data.get("operation") if isinstance(self.input_data, dict) else None
        return named if isinstance(named, str) else None

    @property
    def documents(self) -> int | None:
        """How many documents a queued operation is for."""
        count = self.input_data.get("document_count") if isinstance(self.input_data, dict) else None
        return count if isinstance(count, int) else None

    @property
    def copies_metadata(self) -> bool:
        """Whether the file takes the metadata of a document along, as a rotation does."""
        overrides = self.input_data.get("overrides") if isinstance(self.input_data, dict) else None
        return isinstance(overrides, dict) and "title" in overrides

    @property
    def leaves_number(self) -> bool:
        """Whether the file is told to go without a taken serial number, as copies are."""
        overrides = self.input_data.get("overrides") if isinstance(self.input_data, dict) else None
        return isinstance(overrides, dict) and overrides.get("skip_asn_if_exists") is True

    @property
    def source(self) -> int | None:
        """The document the task works on: the number a ``{id}_...`` file name starts with."""
        head = self.filename.partition("_")[0]
        return int(head) if head.isascii() and head.isdigit() else None

    @property
    def output(self) -> int | None:
        """The output file (from 0) of a ``{id}_edit_{n}.pdf``, whose ``n`` counts from 1."""
        number = self.filename.partition("_edit_")[2].removesuffix(".pdf")
        return int(number) - 1 if number.isascii() and number.isdigit() else None


class _Copy(BaseModel):
    id: int
    original_file_name: str | None
    added: AwareDatetime

    @property
    def name(self) -> str:
        return self.original_file_name or ""


class _User(BaseModel):
    id: int


class _Version(BaseModel):
    id: int
    added: AwareDatetime
    is_root: bool


class _Changed(BaseModel):
    id: int
    versions: list[_Version]


class _FileName(BaseModel):
    original_filename: str | None


class _Entry(BaseModel):
    """An entry of a document's history; a change the system made has no actor."""

    changes: JsonValue
    actor: _User | None

    @property
    def version(self) -> JsonValue:
        """The version the entry says was added, if any."""
        added = self.changes.get("Version Added") if isinstance(self.changes, dict) else None
        return added[-1] if isinstance(added, list) and added else None


class _History(RootModel[list[_Entry]]):
    pass


class _Settings(BaseModel):
    user: _User


type Match = Callable[[_Task], bool]

CONSUME = "consume_file"
REPROCESS = "reprocess_document"
MERGED: Final = "_merged.pdf"
# Paperless names the merged file after the documents, joined and cut to this many characters.
MERGED_NAME_LIMIT: Final = 100


# The queued operations that hand over files a copy could be taken for.
COPYING: Final = frozenset({"edit_pdf", "merge", "remove_password", "split", "delete_pages", None})
# The queued operations that hand over files a rotation could be taken for.
ROTATING: Final = frozenset({"rotate", None})
BULK = "bulk_update"
NO_USER: Final = "the current user is not known"
NO_TIMES: Final = "the times of its queued task are not known"
NOT_OWN: Final = "a file was not handed over by its queued task"
AT_ONCE: Final = "another operation of this user ran at the same time"
UNCOMPARED: Final = "its tasks could not be compared"
BROKE: Final = "its task could not be followed"
PENDING: Final = "files of earlier operations of this user may still come"
TASKED: Final = "a task of its own makes a file of one of these names"
NO_HISTORY: Final = "the history, which names who added a version, may not be read"
UNFINISHED: Final = (
    "another rotation of this user started and has no end in the task list (it still runs, or it "
    "was cut off)"
)
UNREADABLE: Final = "document {document} cannot be read any more"
SEVERAL: Final = "versions {versions} of document {document} could both be the rotation"
# How long a file may wait for its consumption: operations before that are taken to be done.
BACKLOG: Final = timedelta(hours=1)
PART: Final = re.compile(r"(\d+_edit_)\d+\.pdf")
# The first (or only) file an operation that makes new documents hands over, and a new version.
FIRST_FILE: Final = re.compile(r"\d+(_\d+)*_(edit_1|unprotected|merged)\.pdf")
VERSION_FILE: Final = re.compile(r"\d+_edited\.pdf")


@dataclass(frozen=True, slots=True)
class _Copies:
    """How the files of a request for new documents are told from those of others.

    Attributes:
        match: Every file that could be one of them, also one too many.
        copies: Whether Paperless tells the consumer to leave a taken serial number out.
        names: The name of each file, if none is left out.
        included: The documents the name of a merged file stands for, None where it was cut.
    """

    match: Match
    copies: bool = True
    names: tuple[str, ...] = ()
    included: tuple[int, ...] | None = None


def _rotated(document_id: int) -> str:
    """The file name of the version a rotation makes."""
    return f"{document_id}_rotated.pdf"


def _named(names: Sequence[str]) -> Match:
    wanted = frozenset(names)
    return lambda task: task.filename in wanted


def _merged(document_ids: Sequence[int]) -> Match:
    return lambda task: _included(task.filename, document_ids) is not None


def _included(filename: str, document_ids: Sequence[int]) -> tuple[int, ...] | None:
    """The documents a merged file is named after, None if it is not of a merge of these.

    Paperless leaves out what it cannot merge, so the name holds some of the ids, in their order.
    Empty for a name that was cut: which ids came after the cut is not known.
    """
    joined, dot, rest = filename.rpartition(MERGED)
    if not dot or rest or len(joined) > MERGED_NAME_LIMIT:
        return None
    *parts, last = joined.split("_")
    left = iter([str(n) for n in document_ids])
    if not all(part in left for part in parts):
        return None
    if len(joined) == MERGED_NAME_LIMIT:
        return () if any(n.startswith(last) for n in left) else None
    return tuple(int(n) for n in (*parts, last)) if last in left else None


def _like(names: Sequence[str]) -> tuple[str, Callable[[str], bool]]:
    """How the file names of a request start, and whether a name could be one, also one too many."""
    part = PART.fullmatch(names[0])
    if part is None:
        return names[0], lambda name: name == names[0]
    start = part[1]
    return start, lambda name: name.startswith(start) and PART.fullmatch(name) is not None


def _failure(what: str) -> Callable[[str], Validation]:
    return lambda _: not_applied(what)


def edit_failed(task_id: str) -> Validation:
    """The error for an edit whose queued task failed."""
    return task_failed(task_id, "a file that is not a PDF or a page that does not exist")


def _password_failed(task_id: str) -> Validation:
    return task_failed(task_id, "a wrong password or a file that is not a PDF")


def _refused() -> PermissionDenied:
    return PermissionDenied(REFUSED, hint=REFUSED_HINT)


def not_editable() -> Validation:
    """The error for an edit Paperless could not perform (it answers a plain-text 400)."""
    return Validation(
        "Paperless could not edit the file: it may not be a PDF.",
        hint="Only PDF files can be edited; check the document's type.",
    )


def wrong_password() -> Validation:
    """The error for a password removal Paperless could not perform (a plain-text 400)."""
    return Validation(
        "Paperless could not remove the password: it is wrong, or the file is not a PDF.",
        hint="Check the password; it is case sensitive.",
    )


def _plain(error: Validation, replacement: Validation) -> Validation:
    """``error`` unless it carries no details of Paperless's, then ``replacement`` instead."""
    return replacement if error.message == f"{REJECTED}." else error


class PaperlessPdfWriter:
    """``PdfWriter`` over the Paperless REST API."""

    def __init__(
        self,
        client: PaperlessClient,
        *,
        settle_seconds: float = SETTLE_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Write through ``client``; the other arguments bound the lookup of the new tasks."""
        self._client: PaperlessClient = client
        self._settle: float = settle_seconds
        self._poll: float = poll_seconds
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._clock: Callable[[], float] = clock
        self._waiter: TaskWaiter = TaskWaiter(
            client, poll_seconds=poll_seconds, sleep=sleep, clock=clock
        )
        self._lock: asyncio.Lock = asyncio.Lock()

    async def rotate_documents(
        self, document_ids: Sequence[int], degrees: int, *, original_files: bool = False
    ) -> StartedTasks:
        """See ``PdfWriter.rotate_documents``: one ``{id}_rotated.pdf`` task per document."""
        body: dict[str, JsonValue] = {
            "documents": list(document_ids),
            "degrees": degrees,
            "source_mode": _mode(explicit=original_files),
        }
        names = [_rotated(n) for n in document_ids]
        return await self._run(
            "rotate",
            body,
            CONSUME,
            _named(names),
            len(names),
            failed=_failure("rotation"),
            versions=names,
        )

    async def merge_documents(
        self,
        document_ids: Sequence[int],
        *,
        metadata_document_id: int | None,
        archive_fallback: bool = False,
        original_files: bool = False,
    ) -> StartedTasks:
        """See ``PdfWriter.merge_documents``: one task, the file named after the documents."""
        body: dict[str, JsonValue] = {
            "documents": list(document_ids),
            "metadata_document_id": metadata_document_id,
            "archive_fallback": archive_fallback,
            "source_mode": _mode(explicit=original_files),
        }
        joined = "_".join(str(n) for n in document_ids)
        started = await self._run(
            "merge",
            body,
            CONSUME,
            _merged(document_ids),
            1,
            failed=_failure("merge"),
            merged=document_ids,
            own=_Copies(
                _merged(document_ids),
                names=(f"{joined[:MERGED_NAME_LIMIT]}{MERGED}",),
                included=tuple(document_ids) if len(joined) <= MERGED_NAME_LIMIT else None,
            ),
        )
        # The file name starts with a document, but the result is a document of its own.
        return started.model_copy(update={"sources": ()})

    async def edit_pdf(
        self,
        document_id: int,
        pages: Sequence[PageSlot],
        *,
        output: FileOutput,
        version_id: int | None = None,
        keep_metadata: bool = True,
    ) -> StartedTasks:
        """See ``PdfWriter.edit_pdf``: a task per output file.

        As a new version the file is ``{id}_edited.pdf``; as new documents ``{id}_edit_{n}.pdf``
        for every output file, which is how many files the pages go to.
        """
        body: dict[str, JsonValue] = {
            **_source(document_id, version_id),
            "operations": [{"page": p.page, "rotate": p.rotate, "doc": p.document} for p in pages],
            "update_document": output == "new_version",
            "include_metadata": _metadata(output, keep=keep_metadata),
        }
        names, own = [f"{document_id}_edited.pdf"], None
        if output == "new_document":
            parts = re.compile(rf"{document_id}_edit_\d+\.pdf")
            count = len({p.document for p in pages})
            names = [f"{document_id}_edit_{n}.pdf" for n in range(1, count + 1)]
            own = _Copies(
                lambda task: parts.fullmatch(task.filename) is not None, names=tuple(names)
            )
        try:
            return await self._run(
                "edit_pdf",
                body,
                CONSUME,
                _named(names),
                len(names),
                failed=edit_failed,
                own=own,
            )
        except Validation as error:
            raise _plain(error, not_editable()) from None

    async def remove_password(
        self,
        document_id: int,
        password: str,
        *,
        output: FileOutput,
        version_id: int | None = None,
        keep_metadata: bool = True,
    ) -> StartedTasks:
        """See ``PdfWriter.remove_password``: one ``{id}_unprotected.pdf`` task, if any.

        A wrong password is a 400 with a plain-text body; a file without a password is a 200
        that starts no task.
        """
        body: dict[str, JsonValue] = {
            **_source(document_id, version_id),
            "password": password,
            "update_document": output == "new_version",
            "include_metadata": _metadata(output, keep=keep_metadata),
        }
        name = f"{document_id}_unprotected.pdf"
        match = _named([name])
        own = _Copies(match, copies=False, names=(name,)) if output == "new_document" else None
        try:
            return await self._run(
                "remove_password",
                body,
                CONSUME,
                match,
                1,
                failed=_password_failed,
                own=own,
            )
        except Validation as error:
            raise _plain(error, wrong_password()) from None

    async def reprocess_documents(
        self, document_ids: Sequence[int], *, remote_ocr: bool
    ) -> StartedTasks:
        """See ``PdfWriter.reprocess_documents``: one ``reprocess_document`` task per document."""
        body: dict[str, JsonValue] = {"documents": list(document_ids), "remote_ocr": remote_ocr}
        return await self._run(
            "reprocess",
            body,
            REPROCESS,
            None,
            len(document_ids),
            failed=_failure("reprocessing"),
        )

    async def _run(  # noqa: PLR0913 -- the request, the tasks it starts and the failure it may be
        self,
        endpoint: str,
        body: dict[str, JsonValue],
        kind: str,
        match: Match | None,
        expected: int,
        *,
        failed: Callable[[str], Validation],
        merged: Sequence[int] = (),
        own: _Copies | None = None,
        versions: Sequence[str] = (),
    ) -> StartedTasks:
        """Send the request, then collect the new tasks of ``kind`` that ``match``.

        Serialised, so that two requests cannot share a mark. The lock has to span the wait for a
        queued task (3.5) and the lookup, not only the mark and the request: another request that
        posted in between would make file tasks of the same names, which this one would take.
        The wait is bounded (``WAIT_SECONDS``), and ends in an unconfirmed result, not an error.
        The mark is the newest task of this user and kind before the request; new tasks are
        those after it. Nothing that goes wrong in reading the tasks is raised once the request
        was accepted: it is reported in the result, because an error would invite a repeat of
        work that is already queued. ``own`` asks whether the tasks are surely the request's.
        Of ``versions``, the files of a rotation, those without a task are named in the result.
        """
        async with self._lock:
            user = await self._user()
            mark, problem = await self._mark(kind, user)
            try:
                queued = await self._client.call("POST", f"{BASE}{endpoint}/", Queued, json=body)
            except PermissionDenied:
                raise _refused() from None
            found: list[_Task] = []
            try:
                return await self._lookup(
                    queued,
                    failed,
                    found,
                    mark=mark,
                    problem=problem,
                    kind=kind,
                    user=user,
                    match=match,
                    expected=expected,
                    merged=merged,
                    own=own,
                    versions=versions,
                )
            except Validation:
                raise
            except Exception as error:  # noqa: BLE001 -- accepted: say so, whatever goes wrong
                logger.error(  # noqa: TRY400 -- the type alone: a message could hold data
                    "%s: its tasks could not be looked up: %s", endpoint, type(error).__name__
                )
                return _started(found, expected, merged, "failed")

    async def _lookup(  # noqa: PLR0913 -- what is looked for
        self,
        queued: Queued,
        failed: Callable[[str], Validation],
        found: list[_Task],
        *,
        mark: _Task | None,
        problem: LookupProblem | None,
        kind: str,
        user: int | None,
        match: Match | None,
        expected: int,
        merged: Sequence[int],
        own: _Copies | None,
        versions: Sequence[str],
    ) -> StartedTasks:
        """The tasks of an accepted request; ``found`` holds what is known if it breaks."""
        try:
            unconfirmed = await self._waiter.confirmed(queued, failed)
        except Validation:
            raise
        except Exception as error:  # noqa: BLE001 -- accepted: its queued task can still be followed
            logger.error("a queued task could not be followed: %s", type(error).__name__)  # noqa: TRY400 -- the type alone
            unconfirmed = Unconfirmed(task_id=queued.task_id, reason=BROKE)
        if unconfirmed is not None and problem != "forbidden":
            # Accepted, but not followed to its end: hand the queued task back, do not raise,
            # even when the mark failed, because the queued task is still the one to follow.
            # A user who may not view tasks cannot follow it: that stays "forbidden".
            return StartedTasks(
                ids=(unconfirmed.task_id,),
                expected=expected,
                problem="unconfirmed",
                detail=unconfirmed.reason,
            )
        if problem is not None:
            return StartedTasks(ids=(), expected=expected, problem=problem)
        deadline = self._clock() + self._settle
        while True:
            try:
                seen = await self._tasks_after(mark, kind, user)
            except PaperlessMcpError:
                # Fewer than expected were found, or the loop would have ended.
                return _started(found, expected, merged, "failed")
            found[:] = _first_of_each(seen, match)
            left = deadline - self._clock()
            if len(found) >= expected or left <= 0:
                started = _started(found, expected, merged)
                if own is None:
                    names = tuple(n for n in versions if n not in {t.filename for t in found})
                    files = CopiedFiles(request=queued.task_id, names=names) if names else None
                    return started.model_copy(update={"files": files})
                if not found:
                    files = CopiedFiles(
                        request=queued.task_id, names=own.names, included=own.included
                    )
                    return started.model_copy(update={"files": files})
                doubt = await self._doubt(queued.task_id, user, seen, expected, own)
                return started.model_copy(update={"doubt": doubt})
            await self._sleep(min(self._poll, left))

    async def _doubt(
        self, queued: str, user: int | None, seen: list[_Task], expected: int, own: _Copies
    ) -> str | None:
        """Why the files may be another request's, None if they are this one's.

        Nothing names the request a file belongs to. But its queued task hands the files over, so
        they were made while it ran; and only this user's other copies at that time, or files of
        the same names, could be taken for them.
        """
        if user is None:
            return NO_USER
        files = [task for task in seen if own.match(task)]
        if len(files) != expected:
            return f"{len(files)} files of these names were handed over instead of {expected}"
        try:
            page = await self._client.get(TASKS, Page[_Task], params={"task_id": queued})
            rows = [row for row in page.results if row.task_id == queued]
            bulk = (await self._operations()).results
        except PaperlessMcpError:
            return UNCOMPARED
        ran = [(row.date_started, row.date_done) for row in rows]
        if len(ran) != 1 or ran[0][0] is None or ran[0][1] is None:
            return NO_TIMES
        start, end = ran[0][0], ran[0][1]
        if not all(
            start <= task.created <= end and (task.leaves_number or not own.copies)
            for task in files
        ):
            return NOT_OWN
        others = [
            row
            for row in _copying(bulk, queued, {None, user})
            if row.date_started is not None
            and row.date_started <= end
            and (row.date_done is None or row.date_done >= start)
        ]
        return AT_ONCE if others else None

    async def _operations(self) -> Page[_Task]:
        """The newest queued operations, of every user."""
        params: dict[str, str | int] = {
            "task_type": BULK,
            "ordering": "-date_created",
            "page_size": LOOKBACK,
        }
        return await self._client.get(TASKS, Page[_Task], params=params)

    async def find_copies(self, files: CopiedFiles, *, final: bool = False) -> FoundCopies:
        """See ``PdfWriter.find_copies``: by ``original_file_name``, owner and ``added``."""
        user = await self._user()
        operations = await self._operations()
        own = next((row for row in operations.results if row.task_id == files.request), None)
        if user is None or own is None or own.date_started is None:
            return _doubtful(files.names, NO_USER if user is None else NO_TIMES)
        start = own.date_started
        prefix, like = _like(files.names)
        rows = await self._copies(user, start, original_filename__istartswith=prefix)
        found = [row for row in rows if like(row.name)]
        ids = {row.name: row.id for row in found}
        if len(found) > len(files.names) or len(found) > len(ids) or ids.keys() - {*files.names}:
            listed = ", ".join(str(n) for n in sorted(row.id for row in found))
            expected = len(files.names)
            doubt = f"documents {listed} have names of these files, of which there are {expected}"
            return _doubtful(files.names, doubt)
        whole = final or not {*files.names} - ids.keys()
        doubt = await self._taken_for(user, start, operations, files, whole=whole)
        if doubt is not None:
            return _doubtful(files.names, doubt)
        return FoundCopies(ids=tuple(ids.get(name) for name in files.names))

    async def find_rotations(self, search: RotationSearch, *, final: bool = False) -> FoundCopies:
        """See ``PdfWriter.find_rotations``: by place, time, file name and the history.

        The versions are read first: what could have made one of them then shows up in the lists
        of operations and tasks that are read afterwards. What a version is does not change, so it
        is read once per search.
        """
        before = search.before
        params: dict[str, str | int] = {
            "id__in": ",".join(map(str, before)),
            "fields": "id,versions",
            "page_size": LOOKBACK,
        }
        rows = (await self._client.get(BASE, Page[_Changed], params=params)).results
        listed = {row.id: row.versions for row in rows}
        search.user = search.user or await self._user()
        operations = await self._operations()
        own = next((row for row in operations.results if row.task_id == search.request), None)
        if search.user is None or own is None or own.date_started is None:
            return _doubtful(before, NO_USER if search.user is None else NO_TIMES)
        start, named = own.date_started, dict[int, list[int]]()
        for number, newest in before.items():
            if number not in listed:
                return _doubtful(before, UNREADABLE.format(document=number))
            newer = takewhile(lambda v, last=newest: v.id != last, listed[number])
            named[number] = await self._named_rotations(number, newer, start, search)
        ids = dict[int, int | None].fromkeys(before)
        for number, versions in named.items() if final or all(named.values()) else ():
            try:
                made = await self._rotations(number, versions, search)
            except PermissionDenied, Validation:  # 400: the audit log is off
                return _doubtful(before, NO_HISTORY)
            if len(made) > 1:
                several = ", ".join(map(str, made))
                return _doubtful(before, SEVERAL.format(versions=several, document=number))
            ids[number] = made[0] if made else None
        whole = final or None not in ids.values()
        doubt = await self._rotated_else(search, search.user, start, operations, whole=whole)
        if doubt is not None:
            return _doubtful(before, doubt)
        return FoundCopies(ids=tuple(ids.values()))

    async def _named_rotations(
        self,
        document_id: int,
        versions: Iterable[_Version],
        since: datetime,
        search: RotationSearch,
    ) -> list[int]:
        """Those of the versions, added since then, that have the file name of a rotation."""
        late = [v.id for v in versions if not v.is_root and v.added >= since]
        for version in late:
            if version in search.names:
                continue
            name = ""  # deleted meanwhile
            with contextlib.suppress(NotFound):
                path = f"{BASE}{document_id}/metadata/"
                file = await self._client.get(path, _FileName, params={"version": version})
                name = file.original_filename or ""
            search.names[version] = name
        return [version for version in late if search.names[version] == _rotated(document_id)]

    async def _rotations(
        self, document_id: int, versions: Sequence[int], search: RotationSearch
    ) -> list[int]:
        """Those of the versions that the user of the search added, as the history says."""
        if not {*versions} <= search.actors.keys():
            entries = (await self._client.get(f"{BASE}{document_id}/history/", _History)).root
            added = {str(e.version): e.actor.id for e in entries if e.actor is not None}
            search.actors.update({n: added.get(str(n)) for n in versions})
        return [number for number in versions if search.actors[number] == search.user]

    async def _rotated_else(
        self,
        search: RotationSearch,
        user: int,
        start: datetime,
        operations: Page[_Task],
        *,
        whole: bool,
    ) -> str | None:
        """Why these versions may be another request's of this user, None if they are this one's.

        Nothing names the request, and an operation does not name its documents. So there may be no
        other operation since the start, and the earlier ones must be known to be done: as many
        versions as they have documents. Those are counted only if ``whole``.
        """
        since = start - BACKLOG
        names = {_rotated(number) for number in search.before}
        params: dict[str, str | int] = {**self._filters(CONSUME, None), "page_size": LOOKBACK}
        tasks = await self._client.get(TASKS, Page[_Task], params=params)
        if any(
            task.filename in names
            and task.created >= since
            and (task.date_done is None or task.date_done >= start)
            for task in tasks.results
        ):
            return TASKED
        others = [
            row
            for row in operations.results
            if row.task_id != search.request
            and row.owner == user
            and row.operation in ROTATING
            and row.date_started
        ]
        if any(row.date_done is None for row in others):
            return UNFINISHED
        if any(row.date_done and row.date_done >= start for row in others):
            return AT_ONCE
        since, earlier = _since(others, since)
        if any(_ends_after(page, since) for page in (tasks, operations)):
            return UNCOMPARED
        if not earlier or not whole:
            return None
        return await self._unsettled(search, start, earlier, tasks.results)

    async def _unsettled(
        self,
        search: RotationSearch,
        start: datetime,
        earlier: Sequence[_Task],
        tasks: Sequence[_Task],
    ) -> str | None:
        """Why versions of earlier rotations may still come, None if all of them are there.

        Not counted is what could be this request's own, on any of its documents, and what has the
        name of an upload.
        """
        counts = [row.documents for row in earlier]
        since = min(row.date_started for row in earlier if row.date_started)
        params: dict[str, str | int] = {
            "modified__gte": since.isoformat(),
            "fields": "id,versions",
            "page_size": LOOKBACK,
        }
        changed = await self._client.get(BASE, Page[_Changed], params=params)
        if changed.next_page is not None:
            return UNCOMPARED
        uploads = {task.filename for task in tasks if not task.copies_metadata}
        missing = sum(count for count in counts if count is not None)
        for row in changed.results:
            if None in counts or _rotated(row.id) in uploads:
                continue
            own = row.id in search.documents
            versions = [v for v in row.versions if not own or v.added < start]
            with contextlib.suppress(PermissionDenied, Validation, NotFound):
                named = await self._named_rotations(row.id, versions, since, search)
                missing -= len(await self._rotations(row.id, named, search))
            if missing <= 0:
                return None
        return PENDING

    async def _copies(self, user: int, since: datetime, **filters: str) -> list[_Copy]:
        """The documents of this user added since then, as many as one page holds."""
        params: dict[str, str | int] = {
            "owner__id": user,
            "added__gte": since.isoformat(),
            **filters,
            "fields": "id,original_file_name,added",
            "page_size": LOOKBACK,
        }
        page = await self._client.get(BASE, Page[_Copy], params=params)
        return [row for row in page.results if row.added >= since]

    async def _taken_for(
        self,
        user: int,
        start: datetime,
        operations: Page[_Task],
        files: CopiedFiles,
        *,
        whole: bool,
    ) -> str | None:
        """Why documents of these names may be another request's, None if they are this one's.

        Nothing names the request, and an operation does not name its documents. So there may be no
        other operation since the start, and of the earlier ones each must be known to have made
        something else: one first file of another document, or one new version, for each. The
        earlier ones are looked into only if ``whole``.
        """
        _, like = _like(files.names)
        params: dict[str, str | int] = {**self._filters(CONSUME, user), "page_size": LOOKBACK}
        tasks = (await self._client.get(TASKS, Page[_Task], params=params)).results
        if any(like(task.filename) and task.created >= start for task in tasks):
            return TASKED
        oldest = min(row.created for row in operations.results)
        if operations.next_page is not None and oldest > start - BACKLOG:
            return UNCOMPARED
        # 3.5.1 records the caller as owner of every operation. One without an owner is of a user
        # who was deleted since, whose files are not the caller's, or Paperless's own update.
        others = list(_copying(operations.results, files.request, {user}))
        if any(
            row.date_started and (not row.date_done or row.date_done >= start) for row in others
        ):
            return AT_ONCE
        earlier = [
            row.date_started
            for row in others
            if row.date_started and row.date_done and row.date_done >= start - BACKLOG
        ]
        if not earlier or not whole:
            return None
        since = min(earlier)
        made = await self._copies(user, since, original_filename__iendswith=".pdf")
        elsewhere = [
            *(row for row in made if FIRST_FILE.fullmatch(row.name) and not like(row.name)),
            *(t for t in tasks if VERSION_FILE.fullmatch(t.filename) and t.created >= since),
        ]
        return PENDING if len(elsewhere) < len(earlier) else None

    async def _mark(self, kind: str, user: int | None) -> tuple[_Task | None, LookupProblem | None]:
        """The newest task of this user and kind and, if it cannot be read, why not."""
        try:
            return await self._newest(kind, user), None
        except PermissionDenied:
            return None, "forbidden"
        except PaperlessMcpError:
            return None, "failed"

    async def _user(self) -> int | None:
        """The current user's id, or None if it cannot be read (then tasks are not filtered)."""
        try:
            settings = await self._client.get(SETTINGS, _Settings)
        except PaperlessMcpError:
            return None
        return settings.user.id

    @staticmethod
    def _filters(kind: str, user: int | None) -> dict[str, str | int]:
        """The task list filters: the kind and, for files Paperless consumes, the user's own.

        ``ordering=-id`` is ignored by Paperless's task list; ``-date_created`` is its order.
        """
        params: dict[str, str | int] = {"task_type": kind, "ordering": "-date_created"}
        if kind == CONSUME and user is not None:
            params["owner"] = user
        return params

    async def _newest(self, kind: str, user: int | None) -> _Task | None:
        """The newest task of this user and kind, None if there is none."""
        page = await self._client.get(
            TASKS, Page[_Task], params={**self._filters(kind, user), "page_size": 1}
        )
        return page.results[0] if page.results else None

    async def _tasks_after(self, mark: _Task | None, kind: str, user: int | None) -> list[_Task]:
        """The tasks of this user and kind newer than ``mark``, oldest first."""
        params: dict[str, str | int] = {**self._filters(kind, user), "page_size": LOOKBACK}
        if mark is not None:
            params["date_created_after"] = mark.date_created
        page = await self._client.get(TASKS, Page[_Task], params=params)
        newer = (t for t in page.results if mark is None or t.id > mark.id)
        mine = (t for t in newer if kind != CONSUME or user is None or t.owner == user)
        return sorted((t for t in mine if t.task_type == kind), key=lambda t: t.id)


def _doubtful(wanted: Sized, doubt: str) -> FoundCopies:
    nothing = (None,) * len(wanted)
    return FoundCopies(ids=nothing, doubt=doubt, passing=doubt == PENDING)


def _ends_after(page: Page[_Task], since: datetime) -> bool:
    """Whether a list that has more does not reach back that far."""
    return page.next_page is not None and min(row.created for row in page.results) > since


def _since(rows: Sequence[_Task], since: datetime) -> tuple[datetime, list[_Task]]:
    """The rotations that could have made a version since then, or since the first of them began."""
    while True:
        counted = [row for row in rows if row.date_done and row.date_done >= since]
        first = min((row.date_started for row in counted if row.date_started), default=since)
        if first >= since:
            return since, counted
        since = first


def _copying(rows: Sequence[_Task], queued: str, owners: Collection[int | None]) -> Iterator[_Task]:
    """The other operations of these owners that hand over files a copy could be taken for."""
    for row in rows:
        if row.task_id != queued and row.owner in owners and row.operation in COPYING:
            yield row


def _first_of_each(seen: list[_Task], match: Match | None) -> list[_Task]:
    """Those that ``match``, the oldest per file: a request makes each file once."""
    if match is None:
        return seen
    first: dict[str, _Task] = {}
    for task in seen:
        if match(task):
            _ = first.setdefault(task.filename, task)
    return list(first.values())


def _mode(*, explicit: bool) -> str:
    """Which file Paperless takes of a document that is sent by its root: the first or newest."""
    return "explicit_selection" if explicit else "latest_version"


def _metadata(output: FileOutput, *, keep: bool) -> bool:
    """Whether Paperless copies the metadata: a version needs none, the document keeps its own.

    Copying it would hide the task: 3.5.1 cannot record one whose metadata holds a date among the
    custom fields (it logs the error, and the work is done all the same).
    """
    return keep and output == "new_document"


def _source(document_id: int, version_id: int | None) -> dict[str, JsonValue]:
    """The one document of a request, as the version whose file is to be read."""
    return {
        "documents": [document_id if version_id is None else version_id],
        "source_mode": _mode(explicit=version_id is not None),
    }


def _started(
    found: list[_Task],
    expected: int,
    merged: Sequence[int],
    problem: LookupProblem | None = None,
) -> StartedTasks:
    """The oldest ``expected`` tasks: later ones belong to other requests.

    Of merged files the one of the most documents: one of fewer may be another request's.
    """
    if merged:
        found = sorted(found, key=lambda t: -len(_included(t.filename, merged) or merged))
    mine = found[:expected]
    outputs = [task.output for task in mine]
    return StartedTasks(
        ids=tuple(task.task_id for task in mine),
        expected=expected,
        sources=tuple(task.source for task in mine),
        outputs=tuple(n for n in outputs if n is not None),
        included=_included(mine[0].filename, merged) or None if mine and merged else None,
        problem=problem,
    )
