"""Several files, or a directory, uploaded by path in one call (stdio only; decision-50).

Every file is read twice and only one is in memory at a time. First all of them are read, checked
and hashed, so that the duplicate check can look all originals up before it reads the versions and
the trash once for the whole call (``DuplicateFinder.read_for``). Then each is read again, compared
with its hash, checked and sent. A file gets the rules of a single upload by path, and whatever goes
wrong with one is its own report. A known duplicate is never sent from here: the user decides about
each with ``upload_document``.

What one call does is bounded in files, bytes and time. What does not fit is named, and a directory
is continued with a cursor: the files are sorted, so the files after the last one reported are
exactly those that are left. The cursor carries a digest of the directory and the path, so one that
was retyped, also in another Unicode form, is refused instead of skipping files. Reading files and
walking a directory happen in a thread, so that they block neither other calls nor a cancellation.
"""

import asyncio
import functools
import hashlib
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import MAX_UPLOAD_LIMIT
from mcp_server_paperless.domain.errors import (
    AuthenticationFailed,
    Conflict,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UnsupportedVersion,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.uploads import BatchFile, UploadBatch
from mcp_server_paperless.services.allowed_paths import clean_filename
from mcp_server_paperless.services.documents import document_link
from mcp_server_paperless.services.task_outcome import follow
from mcp_server_paperless.services.tasks import MAX_WAIT_SECONDS
from mcp_server_paperless.services.upload_listing import (
    MAX_DEPTH,
    given_path,
    list_allowed,
    shown_path,
)
from mcp_server_paperless.services.upload_source import read_allowed, size_text
from mcp_server_paperless.services.upload_types import sniff
from mcp_server_paperless.services.uploads import NO_DOCUMENT, UNSUPPORTED

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mcp_server_paperless.domain.documents import DocumentUpload, UploadMetadata
    from mcp_server_paperless.domain.duplicates import DuplicateCheck
    from mcp_server_paperless.domain.ports import DocumentWriter
    from mcp_server_paperless.domain.uploads import BatchStatus
    from mcp_server_paperless.services.duplicates import Before, DuplicateFinder, Reading
    from mcp_server_paperless.services.tasks import TaskService
    from mcp_server_paperless.services.upload_listing import Listing
    from mcp_server_paperless.services.uploads import UploadService

logger = logging.getLogger(__name__)

MAX_FILES: Final = 25
# Names of what was left out; the rest is counted.
NAMED: Final = 25


@dataclass(frozen=True, slots=True)
class Limits:
    """What one call does at most; seconds since it began.

    Attributes:
        total_bytes: Read for the files that are reported; one file of the largest size fits.
        listing: The walk of a directory takes at most this long.
        looking: No further file is read to be hashed after this.
        starting: No further file is checked and sent after this. Many clients give a call 60
            seconds, and a result that comes later is lost.
        ending: No task is read after this.
    """

    files: int = MAX_FILES
    total_bytes: int = MAX_UPLOAD_LIMIT
    listing: float = 10
    looking: float = 15
    starting: float = 40
    ending: float = 55


# Answers with which Paperless says that it did not take the file. After any other failure it is not
# known whether it did: a connection can break after the last byte was sent.
NOT_TAKEN: Final = (
    Validation,
    NotFound,
    PermissionDenied,
    AuthenticationFailed,
    Conflict,
    UnsupportedVersion,
)
UNKNOWN: Final = (
    "It is not known whether Paperless accepted the file ({reason}). Look for it in Paperless, or "
    "with list_tasks, before it is uploaded again."
)
CHANGED: Final = "The file changed while it was checked; it was not sent."
UNEXPECTED: Final = "Unexpected error; the server log has the type. It was not sent."
TWIN: Final = "The same content as {path}, which comes earlier in this call."
TWIN_LOST: Final = (
    "The same content as {path}, which comes earlier in this call and ended as {status}; this one "
    "was not sent."
)
FAILED: Final = (
    "Files that failed, with their twins: {count}. next_after does not repeat them; call again with "
    "them as paths, unless their reason says otherwise."
)
DEEPER: Final = (
    "Subdirectories not read because they are more than {depth} levels down: {count}. Give them as "
    "directory."
)
KNOWN: Final = "The same file is already in Paperless: {where}."
UNREAD: Final = (
    "Paperless accepted the file, but its task was not read in time; follow it with get_task."
)
ONE_BY_ONE: Final = (
    "Duplicates were not sent. If the user wants one all the same, use upload_document with its "
    "path and confirm=true."
)
TAKEN: Final = frozenset({"uploaded", "would_upload"})
# Paperless did not answer for this many files in a row: the rest would only wait, too.
SILENT: Final = 2


@dataclass(slots=True)
class _File:
    """One file of the call: hashed, and then its report."""

    path: str
    malformed: Validation | None = None
    sha256: str = ""
    mime_type: str = ""
    size: int = 0
    report: BatchFile | None = None
    found: Before | None = None
    twin: _File | None = None

    def done(self, status: BatchStatus, reason: str | None = None, **more: object) -> None:
        self.report = BatchFile.model_validate(
            {
                "path": shown_path(self.path),
                "status": status,
                "reason": reason,
                "size_bytes": self.size or None,
                "mime_type": self.mime_type or None,
                **more,
            }
        )


@dataclass(slots=True)
class _Call:
    """What one call is about and how far it got; ``start`` is when it began."""

    start: float
    files: list[_File] = field(default_factory=list["_File"])
    left: list[str] = field(default_factory=list[str])
    why: str = ""
    gaps: list[str] = field(default_factory=list[str])

    def reports(self) -> tuple[BatchFile, ...]:
        """What became of the files so far."""
        return tuple(f.report for f in self.files if f.report is not None)

    def lost(self) -> int:
        """How many files failed, or are twins of one that did."""
        return sum(
            f.report is not None and f.report.status == "failed"
            for f in (file.twin or file for file in self.files)
        )

    def cut(self, at: int, why: str) -> None:
        """Leave out the files from ``at`` on."""
        self.left = [*(f.path for f in self.files[at:]), *self.left]
        self.files = self.files[:at]
        self.why = why


def words(error: PaperlessMcpError) -> str:
    """An error as a reason. The hint of a path that was not found names the directories only."""
    return error.message if isinstance(error, NotFound) else error.describe()


def _given(text: str) -> _File:
    """The file a path of the call names."""
    try:
        return _File(given_path(text))
    except Validation as error:
        return _File(text, malformed=error)


def _digest(path: str, directory: str, *, recursive: bool) -> str:
    whole = f"{directory}\0{recursive}\0{path}".encode("utf-8", "surrogatepass")
    return hashlib.sha256(whole).hexdigest()[:12]


def cursor(path: str, directory: str, *, recursive: bool) -> str:
    """Where to go on after ``path``: ASCII only, with a digest of the path and the listing."""
    shown = "".join(
        c if c.isascii() else c.encode("unicode_escape").decode() for c in shown_path(path)
    )
    return f"{shown}#{_digest(path, directory, recursive=recursive)}"


def position(after: str, directory: str, *, recursive: bool) -> str:
    """The path a cursor of this listing stands for.

    Raises:
        Validation: It is none, or not letter for letter the one that was given out.
    """
    shown, _, digest = after.rpartition("#")
    try:
        path = given_path(shown)
    except Validation:
        path = ""
    if not path or digest != _digest(path, directory, recursive=recursive):
        message = "after is not a next_after of this directory."
        hint = (
            "Pass next_after exactly as the last call gave it, with the same directory and "
            "recursive, or leave it out to start over."
        )
        raise Validation(message, hint=hint)
    return path


class BatchUploadService:
    """Uploads several files by path."""

    def __init__(  # noqa: PLR0913 -- the services and settings the use case needs
        self,
        uploads: UploadService,
        writer: DocumentWriter,
        tasks: TaskService,
        duplicates: DuplicateFinder,
        *,
        web_url: str,
        max_bytes: int,
        upload_dirs: Sequence[str] = (),
        limits: Limits | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Send through ``writer`` what lies in ``upload_dirs``, each at most ``max_bytes``.

        ``uploads`` resolves the metadata, ``clock`` measures the limits.
        """
        self._uploads: UploadService = uploads
        self._writer: DocumentWriter = writer
        self._tasks: TaskService = tasks
        self._duplicates: DuplicateFinder = duplicates
        self._web_url: str = web_url
        self._max_bytes: int = max_bytes
        self._dirs: tuple[str, ...] = tuple(upload_dirs)
        self._limits: Limits = limits or Limits()
        self._clock: Callable[[], float] = clock

    async def from_paths(
        self,
        paths: Sequence[str],
        metadata: UploadMetadata,
        *,
        wait_seconds: float = 0,
        dry_run: bool = False,
    ) -> UploadBatch:
        """Upload the files at ``paths`` with the same metadata.

        Raises:
            Validation: Too many files, or the metadata is not acceptable.
            NotFound: A name or linked document of the metadata matches nothing.
            Ambiguous: A name of the metadata matches more than one object.
            PaperlessMcpError: The duplicate check could not run. Nothing was sent.
        """
        start = self._clock()
        given = list(dict.fromkeys(paths))
        if not 0 < len(given) <= self._limits.files:
            message = f"Give 1 to {self._limits.files} paths, not {len(given)}."
            raise Validation(message, hint="Upload more files with more calls.")
        files = [_given(text) for text in given]
        call = await self._upload(start, files, metadata, wait_seconds, dry_run=dry_run)
        again = "call again with the paths in left_out."
        return self._batch(call, again, dry_run=dry_run)

    async def from_directory(  # noqa: PLR0913 -- what to read of the directory and how to send it
        self,
        directory: str,
        metadata: UploadMetadata,
        *,
        recursive: bool = False,
        after: str | None = None,
        wait_seconds: float = 0,
        dry_run: bool = False,
    ) -> UploadBatch:
        """Upload the files in ``directory``, sorted, those after the path ``after``.

        Raises:
            Validation: ``after`` is no cursor of this directory, there are too many entries or
                walking them takes too long, or the metadata is not acceptable.
            NotFound: The directory is not inside the allowlist, or a name of the metadata or a
                linked document matches nothing.
            Ambiguous: A name of the metadata matches more than one object.
            PaperlessMcpError: The duplicate check could not run. Nothing was sent.
        """
        start = self._clock()
        base = str(PurePath(given_path(directory)))
        # Compared as text only: nothing is opened for it.
        last = "" if after is None else position(after, base, recursive=recursive)
        listed = await self._listed(base, recursive=recursive)
        files = [_File(path) for path in listed.files if path > last]
        call = await self._upload(start, files, metadata, wait_seconds, dry_run=dry_run)
        notes: list[str] = []
        if after is not None:
            count = len(listed.files) - len(files)
            notes.append(f"Files that sort before after, not looked at in this call: {count}.")
        if call.left and (lost := call.lost()):
            notes.append(FAILED.format(count=lost))
        if listed.directories:
            count = len(listed.directories)
            notes.append(f"Subdirectories not read: {count}. recursive=true reads them.")
        if listed.deeper:
            notes.append(DEEPER.format(depth=MAX_DEPTH, count=len(listed.deeper)))
        if listed.hidden:
            notes.append(f"Entries left out because their name starts with a dot: {listed.hidden}.")
        batch = self._batch(call, "call again with after=next_after.", dry_run=dry_run)
        return batch.model_copy(
            update={
                "notes": (*batch.notes, *notes),
                "directories": tuple(
                    shown_path(path) for path in (*listed.directories, *listed.deeper)[:NAMED]
                ),
                "next_after": cursor(call.files[-1].path, base, recursive=recursive)
                if call.left
                else None,
            }
        )

    async def _listed(self, directory: str, *, recursive: bool) -> Listing:
        """The walk, in a thread: it stops when nobody waits for it any more."""
        gone = threading.Event()
        walk = functools.partial(self._walk, directory, recursive=recursive, stop=gone.is_set)
        try:
            async with asyncio.timeout(self._limits.listing):
                return await asyncio.to_thread(walk)
        except TimeoutError:
            message = f"Listing the directory took longer than {self._limits.listing:g} seconds."
            hint = "Name the files in paths, or give a smaller directory."
            raise Validation(message, hint=hint) from None
        finally:
            gone.set()

    def _walk(self, directory: str, *, recursive: bool, stop: Callable[[], bool]) -> Listing:
        return list_allowed(directory, self._dirs, recursive=recursive, stop=stop)

    async def _upload(
        self,
        start: float,
        files: Sequence[_File],
        metadata: UploadMetadata,
        wait_seconds: float,
        *,
        dry_run: bool,
    ) -> _Call:
        call = _Call(start=start)
        template = await self._uploads.template(metadata)
        await self._look(call, files)
        await self._send_all(call, template, dry_run=dry_run)
        await self._follow_all(call, wait_seconds)
        for file in call.files:
            first = file.twin.report if file.twin else None
            if first is not None and first.status not in TAKEN:
                reason = TWIN_LOST.format(path=first.path, status=first.status)
                file.done("duplicate", reason)
        return call

    # --- looking ---------------------------------------------------------------------------------

    async def _look(self, call: _Call, files: Sequence[_File]) -> None:
        """Read and hash the files that fit into the call; what is no such file is refused."""
        limits = self._limits
        total = 0
        for index, file in enumerate(files):
            why = ""
            if index >= limits.files:
                why = f"at most {limits.files} files per call"
            elif index and self._clock() - call.start > limits.looking:
                why = f"reading took longer than {limits.looking:g} seconds"
            else:
                try:
                    # shortcut: a file on a mount that hangs is waited for, as in a single upload.
                    await asyncio.to_thread(self._hash, file)
                except PaperlessMcpError as error:
                    file.done("refused", words(error))
                except Exception as error:  # noqa: BLE001 -- one file never hides the others
                    self._unexpected(file, error)
                total += file.size
                if total > limits.total_bytes:
                    why = f"at most {size_text(limits.total_bytes)} per call"
            if why:
                call.left, call.why = [f.path for f in files[index:]], why
                return
            call.files.append(file)

    def _hash(self, file: _File) -> None:
        """Read a file without keeping it: only one is in memory at a time."""
        if file.malformed is not None:
            raise file.malformed
        _ = self._read(file)

    def _read(self, file: _File) -> tuple[str, bytes]:
        """The name and bytes of a file, by every rule of a single upload by path.

        Raises:
            Validation: The name, size or type is not acceptable.
            NotFound: It is not a regular file inside the allowlist.
        """
        found, data = read_allowed(file.path, self._dirs, max_bytes=self._max_bytes)
        file.size = len(data)
        name = clean_filename(found)
        mime_type = sniff(data, name)
        if mime_type is None:
            message = "The file is not a type Paperless can consume."
            raise Validation(message, hint=f"Supported: {UNSUPPORTED}")
        file.mime_type = mime_type
        file.sha256 = hashlib.sha256(data).hexdigest()
        return name, data

    @staticmethod
    def _unexpected(file: _File, error: Exception) -> None:
        logger.error("upload_documents: a file failed: %s", type(error).__name__)
        if file.report is None:
            file.done("failed", UNEXPECTED)

    # --- sending ---------------------------------------------------------------------------------

    async def _send_all(self, call: _Call, template: DocumentUpload, *, dry_run: bool) -> None:
        shas = dict.fromkeys(f.sha256 for f in call.files if f.report is None)
        if not shas:
            return
        reading = await self._duplicates.read_for(shas)
        sent: dict[str, _File] = {}
        silent = 0
        late = False
        for index, file in enumerate(call.files):
            if file.report is not None:
                continue
            if late and self._clock() - call.start > self._limits.starting:
                call.cut(index, f"no file is started after {self._limits.starting:g} seconds")
                return
            if silent == SILENT:
                call.cut(index, f"Paperless did not answer for {SILENT} files in a row")
                return
            late = True
            try:
                await self._send(call, file, template, reading, sent, dry_run=dry_run)
                silent = 0
            except Exception as error:  # noqa: BLE001 -- one file never hides the others
                silent = silent + 1 if isinstance(error, UpstreamUnavailable) else 0
                if not isinstance(error, PaperlessMcpError):
                    self._unexpected(file, error)
                elif file.report is None:
                    file.done("failed", words(error))

    async def _send(  # noqa: PLR0913 -- the file and what the files of a call share
        self,
        call: _Call,
        file: _File,
        template: DocumentUpload,
        reading: Reading,
        sent: dict[str, _File],
        *,
        dry_run: bool,
    ) -> None:
        """Read a file again, check it and send it, unless it is a duplicate.

        Raises:
            PaperlessMcpError: The check or the upload failed.
        """
        sha256 = file.sha256
        if sha256 in sent:
            file.twin = sent[sha256]
            file.done("duplicate", TWIN.format(path=shown_path(file.twin.path)))
            return
        try:
            name, data = await asyncio.to_thread(self._read, file)
        except PaperlessMcpError as error:
            file.done("refused", words(error))
            return
        if file.sha256 != sha256:
            file.done("failed", CHANGED)
            return
        file.found = await self._duplicates.find(sha256, reading)
        call.gaps += file.found.not_checked
        if file.found.count:
            where = KNOWN.format(where=file.found.where())
            file.done("duplicate", where, duplicates=_without_gaps(file.found.checked(None)))
            return
        sent[sha256] = file
        if dry_run:
            file.done("would_upload")
            return
        upload = {"filename": name, "mime_type": file.mime_type, "content": data}
        try:
            task_id = await self._writer.upload_document(template.model_copy(update=upload))
        except NOT_TAKEN:
            del sent[sha256]
            raise
        except Exception as error:
            reason = error.message if isinstance(error, PaperlessMcpError) else "unexpected error"
            file.done("failed", UNKNOWN.format(reason=reason))
            raise
        # A call that is cancelled loses its report; the log keeps what Paperless accepted.
        logger.info("upload_documents: accepted as task %s", task_id)
        file.done("uploaded", task_id=task_id)

    # --- following -------------------------------------------------------------------------------

    async def _follow_all(self, call: _Call, wait_seconds: float) -> None:
        """Read the tasks of what was sent, with one wait for all of them."""
        began = self._clock()
        wait = min(max(wait_seconds, 0), MAX_WAIT_SECONDS)
        for file in call.files:
            report = file.report
            if report is None or report.task_id is None or file.found is None:
                continue
            left = max(0, wait - (self._clock() - began))
            file.report = await self._followed(call, report, file.found, report.task_id, left)

    async def _followed(
        self, call: _Call, report: BatchFile, found: Before, task_id: str, wait: float
    ) -> BatchFile:
        """The report of a file Paperless accepted, with what its task says; never raises."""
        try:
            left = self._limits.ending - (self._clock() - call.start)
            if left <= 0:
                raise TimeoutError  # noqa: TRY301 -- the same as a read that takes too long
            async with asyncio.timeout(left):
                outcome = await follow(
                    self._tasks, task_id, wait, nothing_named=NO_DOCUMENT, duplicates=True
                )
        except TimeoutError:
            return report.model_copy(update={"state": "unknown", "reason": UNREAD})
        check = found.checked(outcome.duplicates)
        call.gaps += check.not_checked
        status: BatchStatus = "uploaded"
        if outcome.state in {"failure", "revoked"}:  # a rejected file has a duplicate report
            status = "failed" if outcome.duplicates is None else "duplicate"
        document = outcome.document_id
        return report.model_copy(
            update={
                "status": status,
                "reason": outcome.message,
                "state": outcome.state,
                "document_id": document,
                "web_url": None if document is None else document_link(self._web_url, document),
                "duplicates": _without_gaps(check),
            }
        )

    # --- reporting -------------------------------------------------------------------------------

    @staticmethod
    def _batch(call: _Call, again: str, *, dry_run: bool) -> UploadBatch:
        files = call.reports()
        notes: list[str] = []
        if call.left:
            notes.append(f"Files not started: {len(call.left)} ({call.why}); {again}")
        if any(f.duplicates is not None and f.duplicates.before_count for f in files):
            notes.append(ONE_BY_ONE)
        return UploadBatch(
            dry_run=dry_run,
            files=files,
            counts=dict(Counter(f.status for f in files)),
            left_out=tuple(shown_path(path) for path in call.left[:NAMED]),
            left_out_count=len(call.left),
            directories=(),
            next_after=None,
            notes=tuple(notes),
            not_checked=tuple(dict.fromkeys(call.gaps)),
        )


def _without_gaps(check: DuplicateCheck) -> DuplicateCheck:
    """A check without what is said once for the whole call."""
    return check.model_copy(update={"not_checked": ()})
