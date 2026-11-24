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
import time
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, JsonValue

from mcp_server_paperless.domain.errors import PaperlessMcpError, PermissionDenied, Validation
from mcp_server_paperless.domain.pdf import StartedTasks
from mcp_server_paperless.paperless.errors import REJECTED
from mcp_server_paperless.paperless.pagination import Page
from mcp_server_paperless.paperless.queued import Queued, TaskWaiter, not_applied, task_failed

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from mcp_server_paperless.domain.pdf import FileOutput, LookupProblem, PageSlot
    from mcp_server_paperless.paperless.client import PaperlessClient

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
    "to add documents, and deleting an original also needs ownership and the permission to delete."
)


class _Task(BaseModel):
    id: int
    task_id: str
    task_type: str
    date_created: str
    owner: int | None = None
    input_data: JsonValue = None

    @property
    def filename(self) -> str:
        data = self.input_data
        name = data.get("filename") if isinstance(data, dict) else None
        return name if isinstance(name, str) else ""

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


class _User(BaseModel):
    id: int


class _Settings(BaseModel):
    user: _User


type Match = Callable[[_Task], bool]

CONSUME = "consume_file"
REPROCESS = "reprocess_document"
MERGED: Final = "_merged.pdf"
# Paperless names the merged file after the documents, joined and cut to this many characters.
MERGED_NAME_LIMIT: Final = 100


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
        match = _named([f"{n}_rotated.pdf" for n in document_ids])
        return await self._run(
            "rotate", body, CONSUME, match, len(document_ids), failed=_failure("rotation")
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
        started = await self._run(
            "merge",
            body,
            CONSUME,
            _merged(document_ids),
            1,
            failed=_failure("merge"),
            merged=document_ids,
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
        names = [f"{document_id}_edited.pdf"]
        if output == "new_document":
            count = len({p.document for p in pages})
            names = [f"{document_id}_edit_{n}.pdf" for n in range(1, count + 1)]
        try:
            return await self._run(
                "edit_pdf", body, CONSUME, _named(names), len(names), failed=edit_failed
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
        try:
            return await self._run(
                "remove_password",
                body,
                CONSUME,
                _named([f"{document_id}_unprotected.pdf"]),
                1,
                failed=_password_failed,
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
    ) -> StartedTasks:
        """Send the request, then collect the new tasks of ``kind`` that ``match``.

        Serialised, so that two requests cannot share a mark. The lock has to span the wait for a
        queued task (3.5) and the lookup, not only the mark and the request: another request that
        posted in between would make file tasks of the same names, which this one would take.
        The wait is bounded (``WAIT_SECONDS``), and ends in an unconfirmed result, not an error.
        The mark is the newest task of this user and kind before the request; new tasks are
        those after it. Nothing that goes wrong in reading the tasks is raised once the request
        was accepted: it is reported in the result, because an error would invite a repeat of
        work that is already queued.
        """
        async with self._lock:
            user = await self._user()
            mark, problem = await self._mark(kind, user)
            try:
                queued = await self._client.call("POST", f"{BASE}{endpoint}/", Queued, json=body)
            except PermissionDenied:
                raise _refused() from None
            unconfirmed = await self._waiter.confirmed(queued, failed)
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
            found: list[_Task] = []
            deadline = self._clock() + self._settle
            while True:
                try:
                    found = await self._tasks_after(mark, kind, user, match)
                except PaperlessMcpError:
                    # Fewer than expected were found, or the loop would have ended.
                    return _started(found, expected, merged, "failed")
                left = deadline - self._clock()
                if len(found) >= expected or left <= 0:
                    return _started(found, expected, merged)
                await self._sleep(min(self._poll, left))

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

    async def _tasks_after(
        self, mark: _Task | None, kind: str, user: int | None, match: Match | None
    ) -> list[_Task]:
        """The tasks of this user and kind newer than ``mark`` that ``match``, oldest first."""
        params: dict[str, str | int] = {**self._filters(kind, user), "page_size": LOOKBACK}
        if mark is not None:
            params["date_created_after"] = mark.date_created
        page = await self._client.get(TASKS, Page[_Task], params=params)
        newer = (t for t in page.results if mark is None or t.id > mark.id)
        mine = (t for t in newer if kind != CONSUME or user is None or t.owner == user)
        ordered = sorted(
            (t for t in mine if t.task_type == kind and (match is None or match(t))),
            key=lambda t: t.id,
        )
        if match is None:
            return ordered
        # A request makes each file once: a second task for it is another request's.
        first: dict[str, _Task] = {}
        for task in ordered:
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
