"""The files in a directory of uploads, by name (decision-50).

A listing is advice and grants nothing: every file it names is resolved, walked to and checked again
by ``upload_source.read_allowed``, so whatever changes in between is noticed there. Names inside the
allowlist are told to the caller; what lies outside is not.

- The directory must lie inside the allowlist (it may be the allowed one itself), by the rules of a
  single file: no ``..``, resolved first, dot names refused, then opened component by component
  without following a link. Every other directory has one answer.
- Names that start with a dot are counted, never named or opened.
- A link to a directory inside the allowlist counts as one. Every other link, and everything that is
  no directory, is listed as a file and judged when it is read: reading a link that leads out gives
  the same answer as a missing file, so the listing tells nothing about what lies outside.
- Real directories are walked before linked ones and each only once, so that loops end and the same
  directory gives the same paths in every call. Directories too deep are named, not read.
"""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import NotFound, Validation
from mcp_server_paperless.services.allowed_paths import (
    SECURE,
    Refused,
    dotted,
    inside,
    inside_real,
    open_directory,
    unreadable,
    unseen,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# Entries read in one walk, whatever they are.
MAX_ENTRIES: Final = 10_000
# Levels of subdirectories; each holds a descriptor while what is below it is read.
MAX_DEPTH: Final = 32
# Not on Windows, where nothing is opened with them (SECURE), but this is imported.
DIRECTORY: Final = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
NO_LINK: Final = getattr(os, "O_NOFOLLOW", 0)
# The escapes of ``shown_path``, and any other backslash.
ESCAPE: Final = re.compile(
    r"\\(?:\\|[nrt]|x[0-9a-f]{2}|u[0-9a-f]{4}|U00(?:0[0-9a-f]|10)[0-9a-f]{4})|(\\)"
)


class StoppedError(Exception):
    """Nobody waits for the walk any more."""


@dataclass(frozen=True, slots=True)
class Listing:
    """What is in a directory.

    Attributes:
        files: Paths below the directory as it was given, sorted; not necessarily files, see the
            module.
        directories: The directories that were not read because the walk is not recursive.
        deeper: The directories that were not read because they are too deep.
        hidden: How many names start with a dot.
    """

    files: tuple[str, ...]
    directories: tuple[str, ...]
    deeper: tuple[str, ...]
    hidden: int


@dataclass(frozen=True, slots=True)
class _Place:
    """A directory to read: its path as it was given and found, and where it really is."""

    path: str
    root: str
    parts: tuple[str, ...]
    depth: int


def shown_path(path: str) -> str:
    """``path`` as it is reported: backslashes and what hides or cannot be sent are escaped."""
    return "".join(
        c.encode("unicode_escape").decode()
        if c == "\\" or unseen(c) or "\ud800" <= c <= "\udfff"
        else c
        for c in path
    )


def given_path(text: str, *, secure: bool = SECURE) -> str:
    """The path that ``shown_path`` shows as ``text``; as it is where paths are refused anyway.

    Raises:
        Validation: A backslash starts no escape that a report holds.
    """

    def plain(found: re.Match[str]) -> str:
        if found[1] is not None:
            message = "The path has a backslash that is no escape of a report."
            hint = "Copy paths as they are reported; a backslash in a name is written twice."
            raise Validation(message, hint=hint)
        return found[0].encode().decode("unicode_escape")

    return ESCAPE.sub(plain, text) if secure else text


class _Walk:
    """One walk: what was found so far and the linked directories still to read."""

    def __init__(  # noqa: PLR0913 -- the seams of the walk
        self,
        roots: Sequence[str],
        *,
        recursive: bool,
        most: int,
        depth: int,
        at: Callable[[str], None],
        stop: Callable[[], bool],
    ) -> None:
        self.roots: tuple[str, ...] = tuple(os.path.realpath(root) for root in roots)
        self.recursive: bool = recursive
        self.most: int = most
        self.depth: int = depth
        self.at: Callable[[str], None] = at
        self.stop: Callable[[], bool] = stop
        self.files: list[str] = []
        self.directories: list[str] = []
        self.deeper: list[str] = []
        self.hidden: int = 0
        self.entries: int = 0
        self.seen: set[tuple[int, int]] = set()
        self.linked: list[_Place] = []

    def all(self, place: _Place) -> Listing:
        """Read a directory and what belongs to it.

        Raises:
            OSError: The directory is not there, or a link is on the way to it.
            StoppedError: The walk was stopped.
            Validation: The walk has too many entries.
        """
        self.read(open_directory(place.root, place.parts), place)
        while self.linked:
            self.enter(self.linked.pop(0))
        return Listing(
            files=tuple(sorted(self.files)),
            directories=tuple(sorted(self.directories)),
            deeper=tuple(sorted(self.deeper)),
            hidden=self.hidden,
        )

    def enter(self, place: _Place, parent: int | None = None) -> None:
        """Read a subdirectory, opened relative to ``parent``, or from the root if it was linked."""
        if not self.recursive:
            self.directories.append(place.path)
            return
        if place.depth > self.depth:
            self.deeper.append(place.path)
            return
        try:
            if parent is None:
                held = open_directory(place.root, place.parts)
            else:
                held = os.open(place.parts[-1], DIRECTORY | NO_LINK, dir_fd=parent)
            self.read(held, place)
        except OSError:  # no directory any more: reading it as a file gives the one refusal
            self.files.append(place.path)

    def read(self, held: int, place: _Place) -> None:
        """Sort the entries of the directory ``held``, read its subdirectories and close it."""
        try:
            info = os.fstat(held)
            if (info.st_dev, info.st_ino) in self.seen:
                return
            names = self._names(held)
            self.seen.add((info.st_dev, info.st_ino))
            self.at("listed")
            real = [sub for name in sorted(names) if (sub := self._sorted(place, name, held))]
            self.at("looked")
            for sub in real:
                self.enter(sub, held)
        finally:
            os.close(held)

    def _names(self, held: int) -> list[str]:
        names: list[str] = []
        with os.scandir(held) as entries:
            for entry in entries:
                self._go_on()
                self.entries += 1
                if self.entries > self.most:
                    message = f"The directory has more than {self.most} entries."
                    hint = "Name the files in paths, or give a smaller directory."
                    raise Validation(message, hint=hint)
                names.append(entry.name)
        return names

    def _go_on(self) -> None:
        if self.stop():
            raise StoppedError

    def _sorted(self, place: _Place, name: str, held: int) -> _Place | None:
        """Note an entry; a real directory is given back."""
        self._go_on()
        if dotted(name):
            self.hidden += 1
            return None
        path = f"{place.path}/{name}"
        try:
            mode = os.stat(name, dir_fd=held, follow_symlinks=False).st_mode
        except OSError:  # gone: reading it says so
            mode = 0
        if stat.S_ISDIR(mode):
            return _Place(path, place.root, (*place.parts, name), place.depth + 1)
        if stat.S_ISLNK(mode) and (target := self._directory(path, place.depth + 1)):
            self.linked.append(target)
        else:
            self.files.append(path)
        return None

    def _directory(self, path: str, depth: int) -> _Place | None:
        """Where a link leads, if that is a directory inside the allowlist."""
        try:
            found = inside_real(os.path.realpath(path), self.roots)
            if found is None or unreadable(found[1]):
                return None
            self.at("followed")
            os.close(open_directory(*found))
        except OSError, ValueError:
            return None
        return _Place(path, *found, depth)


def list_allowed(  # noqa: PLR0913 -- the seams of the tests, all defaulted
    directory: str,
    roots: Sequence[str],
    *,
    recursive: bool = False,
    most: int = MAX_ENTRIES,
    depth: int = MAX_DEPTH,
    secure: bool = SECURE,
    at: Callable[[str], None] = lambda _stage: None,
    stop: Callable[[], bool] = lambda: False,
) -> Listing:
    """What is in ``directory``, which must lie inside ``roots``.

    ``at`` is a test seam, called with the stage just reached: ``resolved`` (the path is approved),
    ``listed`` (the names of a directory were read), ``followed`` (a link was resolved) and
    ``looked`` (a directory is fully sorted). ``stop`` is asked for every entry.

    Raises:
        Validation: No allowlist, a relative path, a platform without safe opening, or more than
            ``most`` entries.
        StoppedError: ``stop`` said so.
        NotFound: The path is outside the allowlist or no directory that can be read.
    """
    if not roots:
        message = "Uploading by path is not enabled."
        hint = "The operator enables paths with PAPERLESS_MCP_UPLOAD_DIRS."
        raise Validation(message, hint=hint)
    if "\x00" in directory:
        message = "The path contains a NUL byte."
        raise Validation(message, hint="Give the plain path of the directory.")
    if not Path(directory).is_absolute():
        message = "The path must be absolute."
        raise Validation(message, hint="Give the full path starting with / (or a drive).")
    if not secure:
        message = "Uploading by path is not supported on this platform."
        raise Validation(message, hint="Send each file as content_base64 with upload_document.")
    walk = _Walk(roots, recursive=recursive, most=most, depth=depth, at=at, stop=stop)
    try:
        if ".." in PurePath(directory).parts:
            raise Refused  # noqa: TRY301 -- one answer for every refusal
        found = inside(os.path.realpath(directory), roots)
        if found is None or unreadable(found[1]):
            raise Refused  # noqa: TRY301 -- one answer for every refusal
        at("resolved")
        return walk.all(_Place(str(PurePath(directory)), *found, 0))
    except OSError, ValueError, Refused:  # ValueError: a path that cannot be encoded
        message = "No directory was found at that path inside the allowed directories."
        hint = f"Give the absolute path of a directory inside one of: {', '.join(roots)}."
        raise NotFound(message, hint=hint) from None
