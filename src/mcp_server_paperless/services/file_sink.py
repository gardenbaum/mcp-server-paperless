"""Where a download is saved: one file in a directory the operator allowed (stdio; decision-45).

The directory and the name come from a model that reads documents others wrote, so the rules make
the server write nothing but one new plain file, or replace one that was confirmed:

- The directory is found as in ``upload_source``: absolute, without ``..``, resolved inside an
  allowed directory, nothing below it starting with a dot or holding unseen characters, then opened
  component by component without following a link. Every other path gets the same answer. No directory is created.
- The name has the ending of the type Paperless detected, from a fixed table (``.bin`` for any other
  type), so no title, upload or text of a document makes the server write a script, a program or an
  instruction file; and it never starts with a dot.
- The file is written under a random hidden name with mode 0600, and only what is complete gets
  its name: by a hard link, which never replaces anything, or (file systems without them) by
  renaming over an empty file made with ``O_EXCL``. A confirmed replacement renames over exactly
  the file that was looked at; a link, a file with a second name or a special file is never
  replaced. Everything goes through the descriptor of the directory, which is compared with the
  path again before the end.

Known limits: POSIX has no rename or link that compares, so another process of the same user can
interfere in the microseconds after the last look: swap the target of a confirmed replacement (a
link there is replaced, never followed), swap the temporary name (what it put there gets the name),
or move the directory away (the file is then where the directory went). A killed process leaves its
``.mcp-*.part``. The allowed directory itself is opened by its resolved name, as in
``upload_source``, and what is mounted inside it counts as inside. Windows has neither ``dir_fd``
nor ``O_NOFOLLOW``: saving is refused there.
"""

import asyncio
import contextlib
import errno
import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final, final

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    Validation,
    quoted,
)
from mcp_server_paperless.services.allowed_paths import (
    MAX_NAME_BYTES,
    SECURE,
    clean_filename,
    inside,
    open_directory,
    unseen,
    untouched,
)
from mcp_server_paperless.services.transfer import (
    HEAD_BYTES,
    MAX_FILE_BYTES,
    MAX_SECONDS,
    TAIL_BYTES,
    incomplete,
    is_archive,
    no_archive,
    time_limit,
    too_large,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence

MIB: Final = 1024 * 1024
# What stays free on the file system in any case.
RESERVE: Final = 256 * MIB
SHA256_LENGTH: Final = 64
# How file systems say that they have no hard links. Names, as not every platform has every number.
NO_LINKS: Final = frozenset({"EPERM", "ENOTSUP", "EOPNOTSUPP", "ENOSYS", "EMLINK"})
# How they say that a directory cannot be synced, which is no failure.
NO_SYNC: Final = frozenset({"EINVAL", "ENOTSUP", "EOPNOTSUPP"})
NEW: Final = os.O_CREAT | os.O_EXCL
_FULL: Final = "There is not enough space for the file in that directory; nothing was saved."
_DENIED: Final = "The server may not write into that directory; nothing was saved."
_LARGE: Final = "The file is too large for the file system of that directory; nothing was saved."
_ROOM: Final = "Make room or choose another directory; 256 MiB stay free in any case."
_OTHER: Final = "Choose another directory, or ask the operator."
_FAILURES: Final = {
    "ENOSPC": (_FULL, _ROOM),
    "EDQUOT": (_FULL, _ROOM),
    "EFBIG": (_LARGE, _OTHER),
    "EROFS": (_DENIED, _OTHER),
    "EACCES": (_DENIED, _OTHER),
    "EPERM": (_DENIED, _OTHER),
}

# The endings of every type Paperless consumes; another type is saved as .bin, so that no file is
# lost and no title or upload decides how the system treats it.
UNKNOWN_ENDING: Final = ".bin"
UNNAMED: Final = "document"
_OFFICE: Final = "application/vnd.openxmlformats-officedocument."
_OPEN: Final = "application/vnd.oasis.opendocument."
_ENDINGS: Final[Mapping[str, tuple[str, ...]]] = {
    "application/pdf": (".pdf",),
    "application/zip": (".zip",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/tiff": (".tif", ".tiff"),
    "image/gif": (".gif",),
    "image/bmp": (".bmp",),
    "image/webp": (".webp",),
    "image/heic": (".heic",),
    "text/plain": (".txt",),
    "text/csv": (".csv",),
    "application/csv": (".csv",),
    "text/rtf": (".rtf",),
    "message/rfc822": (".eml",),
    "application/msword": (".doc",),
    "application/vnd.ms-excel": (".xls",),
    "application/vnd.ms-powerpoint": (".ppt",),
    f"{_OFFICE}wordprocessingml.document": (".docx",),
    f"{_OFFICE}spreadsheetml.sheet": (".xlsx",),
    f"{_OFFICE}presentationml.presentation": (".pptx",),
    f"{_OFFICE}presentationml.slideshow": (".ppsx",),
    f"{_OPEN}presentation": (".odp",),
    f"{_OPEN}spreadsheet": (".ods",),
    f"{_OPEN}text": (".odt",),
    f"{_OPEN}graphics": (".odg",),
}

type _Identity = tuple[int, int, int, int]


class System:
    """The calls to the operating system that tests make fail."""

    def write(self, fd: int, data: memoryview) -> int:
        """Write some of ``data``; how much."""
        return os.write(fd, data)

    def fsync(self, fd: int) -> None:
        """Have the file or directory on the disk."""
        os.fsync(fd)

    def link(self, source: str, target: str, directory: int) -> None:
        """Give the file a second name, unless that name exists."""
        os.link(source, target, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)

    def replace(self, source: str, target: str, directory: int) -> None:
        """Rename over whatever has the name."""
        os.replace(source, target, src_dir_fd=directory, dst_dir_fd=directory)

    def unlink(self, name: str, directory: int) -> None:
        """Remove the name."""
        os.unlink(name, dir_fd=directory)

    def close(self, fd: int) -> None:
        """Close the file or directory."""
        os.close(fd)

    def free(self, directory: int) -> int:
        """Bytes this user may still write there."""
        found = os.fstatvfs(directory)
        return found.f_bavail * found.f_frsize

    def date(self, seconds: float) -> str:
        """The day of a time stamp, or nothing for one outside the calendar."""
        try:
            return f", changed {datetime.fromtimestamp(seconds, UTC).date()}"
        except ValueError, OverflowError, OSError:
            return ""


@dataclass(frozen=True, slots=True)
class Expected:
    """What the bytes must be.

    Attributes:
        size: Their number; None if it is not known.
        checksum: Their SHA-256; None, or a value that is no SHA-256, is not compared.
        zip: They are a ZIP archive, which has an end record.
    """

    size: int | None = None
    checksum: str | None = None
    zip: bool = False


@dataclass(frozen=True, slots=True)
class Saved:
    """A file that was saved."""

    filename: str
    size: int
    sha256: str
    replaced: bool


def file_name(save_as: str | None, proposed: str, mime_type: str) -> str:
    """The name to save under: ``save_as``, or the proposed one, cleaned and shortened as needed.

    The ending is always one of the type, never one of a title or an upload: a proposed name with
    another ending gets the right one behind it.

    Raises:
        Validation: The name is a path, is unusable or hidden, or has another ending.
    """
    kind = mime_type.partition(";")[0].strip().lower()
    endings = _ENDINGS.get(kind, (UNKNOWN_ENDING,))
    if save_as is None:
        plain = proposed.replace("/", "_").replace("\\", "_").encode(errors="ignore").decode()
        shown = "".join(c for c in plain if not unseen(c)).strip().lstrip(".").strip()
        suffix = PurePath(shown).suffix
        if suffix.lower() in endings:
            stem = shown.removesuffix(suffix)
        else:
            stem, suffix = shown, endings[0]
        room = MAX_NAME_BYTES - len(suffix)
        name = clean_filename((stem.encode()[:room].decode(errors="ignore") or UNNAMED) + suffix)
    elif "/" in save_as or "\\" in save_as:
        message = "save_as is a file name, not a path."
        raise Validation(message, hint="Name the directory with save_dir.")
    else:
        try:
            name = clean_filename(save_as)
        except UnicodeEncodeError:
            message = "The file name has characters that cannot be stored."
            raise Validation(message, hint="Give another name with save_as.") from None
    if name.startswith("."):
        message = "The file name starts with a dot."
        raise Validation(message, hint="Give another name with save_as.")
    if PurePath(name).suffix.lower() not in endings:
        listed = " or ".join(endings)
        message = f"A file of type {kind} is saved with the ending {listed}."
        raise Validation(message, hint="Change save_as, or leave it out.")
    return name


def _shown(text: str) -> str:
    """``text`` in quotes, whole and exactly as it is.

    Raises:
        Validation: That is not possible.
    """
    shown = quoted(text, len(text))
    if shown[1:-1] != text or not text.isprintable():
        message = "The directory or the file name has characters that a confirmation cannot show."
        hint = "Choose another name with save_as, or another directory."
        raise Validation(message, hint=hint)
    return shown


def _meanwhile() -> PaperlessMcpError:
    message = "The directory or the file changed meanwhile; nothing was saved."
    return PaperlessMcpError(message, hint="Look at the directory, then try again.")


def _appeared() -> PaperlessMcpError:
    message = "A file of that name appeared meanwhile; nothing was replaced."
    return PaperlessMcpError(message, hint="Look at the directory, then try again.")


def _name_of(error: OSError) -> str:
    return errno.errorcode.get(error.errno or 0, "")


def _failure(error: OSError) -> PaperlessMcpError:
    """The client's message: never the system's, which may name paths."""
    default = ("The file could not be written; nothing was saved.", _OTHER)
    message, hint = _FAILURES.get(_name_of(error), default)
    return PaperlessMcpError(message, hint=hint)


async def _blocking[**P, R](function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Run ``function`` in a thread; a cancelled caller goes on only after the thread ended.

    So nothing is written any more once the caller cleans up.

    Raises:
        PaperlessMcpError: The operating system refused.
    """
    running = asyncio.ensure_future(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(running)
    except OSError as error:
        raise _failure(error) from None
    except asyncio.CancelledError:
        while not running.done():
            # Cancelled again (anyio does that until it is heard): still wait.
            with contextlib.suppress(asyncio.CancelledError):
                _ = await asyncio.wait([running])
        _ = running.exception()
        raise


class FileSink:
    """Saves files into the directories the operator allowed."""

    def __init__(  # noqa: PLR0913 -- the limits and seams are injected, keyword-only
        self,
        roots: Sequence[str],
        *,
        max_bytes: int = MAX_FILE_BYTES,
        seconds: float = MAX_SECONDS,
        secure: bool = SECURE,
        system: System | None = None,
        at: Callable[[str], None] = lambda _stage: None,
    ) -> None:
        """Save below ``roots``, at most ``max_bytes`` and ``seconds`` per file.

        ``at`` is a test seam, called with the stage just reached: ``resolved`` (the path is
        approved), ``created`` (the temporary file exists), ``written`` (it is complete),
        ``commit`` (everything was compared) and ``reserved`` (without hard links: the name is
        taken).
        """
        self.roots: tuple[str, ...] = tuple(roots)
        self.max_bytes: int = max_bytes
        self.seconds: float = seconds
        self._secure: bool = secure
        self.system: System = system or System()
        self.at: Callable[[str], None] = at

    @contextlib.asynccontextmanager
    async def open(self, directory: str) -> AsyncGenerator[Target]:
        """The directory to save one file into; what was not finished is removed on exit.

        Raises:
            Validation: Nothing is allowed, or the platform cannot open safely.
            NotFound: The path is no directory inside the allowed ones.
        """
        if not self.roots:
            message = "Saving files is not enabled."
            hint = "The operator enables it with PAPERLESS_MCP_DOWNLOAD_DIRS."
            raise Validation(message, hint=hint)
        if not self._secure:
            message = "Saving files is not supported on this platform."
            raise Validation(message, hint="Read the file in parts with offset.")
        target = Target(self, directory)
        try:
            await _blocking(target.enter)
            yield target
        finally:
            target.leave()


@final
class Target:
    """One file on its way into a directory. Made by ``FileSink.open``."""

    def __init__(self, sink: FileSink, directory: str) -> None:
        """Prepare to save into ``directory``, as the caller named it."""
        self._sink: FileSink = sink
        self._given: str = directory
        self._root: str = ""
        self._parts: tuple[str, ...] = ()
        self._fd: int | None = None
        self._file: int | None = None
        self._temporary: str | None = None
        self._name: str = ""
        self._existing: _Identity | None = None
        self._size: int = 0
        self._digest = hashlib.sha256()

    @property
    def _directory(self) -> int:
        assert self._fd is not None  # noqa: S101 -- set by enter(), which FileSink.open awaits
        return self._fd

    def enter(self) -> None:
        """Find and open the directory."""
        nowhere = NotFound(
            "No usable directory was found at that path inside the allowed directories.",
            hint=(
                "Give the absolute path of an existing directory inside one of: "
                f"{', '.join(self._sink.roots)}."
            ),
        )
        given = PurePath(self._given)
        if not Path(self._given).is_absolute() or ".." in given.parts or "\0" in self._given:
            raise nowhere
        try:
            found = inside(os.path.realpath(self._given), self._sink.roots)
            if found is None or untouched(found[1]):
                raise nowhere
            self._root, self._parts = found
            self._sink.at("resolved")
            self._fd = open_directory(self._root, self._parts)
        except OSError, ValueError:  # ValueError: a path that cannot be encoded
            raise nowhere from None

    def leave(self) -> None:
        """Remove what was not finished and close everything, whatever fails on the way."""
        system = self._sink.system
        if self._file is not None:
            # Network file systems tell of a full disk here, too: what was committed was synced
            # before, and the rest is thrown away.
            with contextlib.suppress(OSError):
                system.close(self._file)
            self._file = None
        if self._fd is None:
            return
        if self._temporary is not None:
            with contextlib.suppress(OSError):
                system.unlink(self._temporary, self._fd)
        with contextlib.suppress(OSError):
            system.close(self._fd)
        self._fd = None

    async def claim(self, name: str, *, confirm: bool) -> None:
        """Decide what becomes of ``name``, before anything is downloaded.

        Raises:
            ConfirmationRequired: A file of that name exists and ``confirm`` is not set.
            Validation: Something that is no plain file has the name, or the replacement cannot be
                shown.
        """
        self._name = name
        self._existing = await _blocking(self._look)
        if self._existing is None:
            return
        file, directory = _shown(name), _shown(self._given)
        if not confirm:
            _, _, size, changed = self._existing
            day = self._sink.system.date(changed / 1e9)
            effect = f"replace the file {file} ({size} bytes{day}) in {directory}"
            raise ConfirmationRequired.for_effect(effect)

    async def write(self, chunks: AsyncIterator[bytes], expected: Expected) -> Saved:
        """Save the bytes under the claimed name.

        Raises:
            PaperlessMcpError: There is no room, the bytes are not the expected ones, something
                changed meanwhile, or the disk refused; then nothing was saved.
        """
        if expected.size is not None and expected.size > self._sink.max_bytes:
            raise too_large()
        await _blocking(self._need, expected.size or 0)
        await _blocking(self._create)
        async with time_limit(self._sink.seconds):
            waiting = bytearray()
            async for chunk in chunks:
                waiting += chunk
                while len(waiting) >= MIB:
                    await _blocking(self._append, bytes(waiting[:MIB]))
                    del waiting[:MIB]
            await _blocking(self._append, bytes(waiting))
        await _blocking(self._complete, expected)
        return Saved(
            self._name, self._size, self._digest.hexdigest(), replaced=self._existing is not None
        )

    def _look(self) -> _Identity | None:
        """What has the name: nothing, or a plain file with no second name."""
        try:
            found = os.stat(self._name, dir_fd=self._directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(found.st_mode) or found.st_nlink != 1:
            message = f"{quoted(self._name)} in that directory is not a plain file of its own."
            raise Validation(message, hint="Choose another name with save_as.")
        return found.st_dev, found.st_ino, found.st_size, found.st_mtime_ns

    def _make(self, name: str) -> int:
        flags = os.O_RDWR | NEW | os.O_NOFOLLOW | os.O_CLOEXEC
        return os.open(name, flags, 0o600, dir_fd=self._directory)

    def _need(self, size: int) -> None:
        """Refuse to write ``size`` bytes if less than the reserve would stay free."""
        if size + RESERVE > self._sink.system.free(self._directory):
            raise PaperlessMcpError(_FULL, hint=_ROOM)

    def _create(self) -> None:
        name = f".mcp-{secrets.token_hex(8)}.part"
        self._file = self._make(name)
        self._temporary = name
        self._sink.at("created")

    def _append(self, data: bytes) -> None:
        assert self._file is not None  # noqa: S101 -- set by _create(), which write() awaits first
        self._size += len(data)
        if self._size > self._sink.max_bytes:
            raise too_large()
        # Again for every piece: the size can be unknown or wrong, and others write, too.
        self._need(len(data))
        self._digest.update(data)
        rest = memoryview(data)
        while rest:
            rest = rest[self._sink.system.write(self._file, rest) :]

    def _complete(self, expected: Expected) -> None:
        """Compare the bytes and the directory, then give the file its name."""
        assert self._file is not None  # noqa: S101 -- set by _create(), which write() awaits first
        self._compare(self._file, expected)
        self._sink.system.fsync(self._file)
        # After the bytes: on FAT the inode number of an empty file changes when data arrives.
        mine = os.fstat(self._file)
        self._sink.at("written")
        self._check_directory()
        temporary = self._temporary or ""
        try:
            found = os.stat(temporary, dir_fd=self._directory, follow_symlinks=False)
        except FileNotFoundError:
            raise _meanwhile() from None
        if (found.st_dev, found.st_ino, found.st_nlink) != (mine.st_dev, mine.st_ino, 1):
            raise _meanwhile()
        self._sink.at("commit")
        self._commit(temporary)
        try:
            self._sink.system.fsync(self._directory)
        except OSError as error:
            if _name_of(error) not in NO_SYNC:
                message = (
                    "The file was saved, but the disk reported an error afterwards; it may be lost."
                )
                hint = "Look at the file before you rely on it."
                raise PaperlessMcpError(message, hint=hint) from None

    def _compare(self, fd: int, expected: Expected) -> None:
        checksum = expected.checksum
        comparable = checksum is not None and len(checksum) == SHA256_LENGTH
        if (expected.size is not None and expected.size != self._size) or (
            comparable and checksum != self._digest.hexdigest()
        ):
            raise incomplete()
        if expected.zip:
            tail = os.pread(fd, TAIL_BYTES, max(0, self._size - TAIL_BYTES))
            if not is_archive(os.pread(fd, HEAD_BYTES, 0), tail, self._size):
                raise no_archive()

    def _check_directory(self) -> None:
        """The held directory is still the one at its place inside the allowed directory."""
        try:
            again = open_directory(self._root, self._parts)
        except OSError:
            raise _meanwhile() from None
        try:
            if not os.path.sameopenfile(again, self._directory):
                raise _meanwhile()
        finally:
            os.close(again)

    def _commit(self, temporary: str) -> None:
        system = self._sink.system
        if self._existing is not None:
            if self._look() != self._existing:
                raise _meanwhile()
            system.replace(temporary, self._name, self._directory)
            self._temporary = None
            return
        try:
            system.link(temporary, self._name, self._directory)
        except FileExistsError:
            raise _appeared() from None
        except OSError as error:
            if _name_of(error) not in NO_LINKS:
                raise
            self._commit_without_links(temporary)
            return
        try:
            system.unlink(temporary, self._directory)
        except OSError:
            message = (
                f"The file was saved, but its temporary name {temporary} could not be removed."
            )
            hint = "Remove that name in the directory; until then the file cannot be replaced."
            raise PaperlessMcpError(message, hint=hint) from None
        self._temporary = None

    def _commit_without_links(self, temporary: str) -> None:
        """Take the name with an empty file, then rename over exactly that file."""
        try:
            reserved = self._make(self._name)
        except FileExistsError:
            raise _appeared() from None
        mine = os.fstat(reserved)
        with contextlib.suppress(OSError):
            self._sink.system.close(reserved)
        ours = (mine.st_dev, mine.st_ino, 0, mine.st_mtime_ns)
        self._sink.at("reserved")
        if self._look() != ours:
            raise _meanwhile()
        try:
            self._sink.system.replace(temporary, self._name, self._directory)
        except OSError:
            if self._look() == ours:
                self._sink.system.unlink(self._name, self._directory)
            raise
        self._temporary = None


# For a server that saves nothing.
NO_SINK: Final = FileSink(())
