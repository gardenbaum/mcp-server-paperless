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
- Real directories are walked before linked ones and each only once, so that loops end and the order
  is the same in every call.
"""

import os
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
    open_directory,
    unreadable,
    unseen,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# Entries read in one walk, whatever they are.
MAX_ENTRIES: Final = 10_000


@dataclass(frozen=True, slots=True)
class Listing:
    """What is in a directory.

    Attributes:
        files: Paths below the directory as it was given, sorted by what is shown of them; not
            necessarily files, see the module.
        directories: The directories that were not read.
        hidden: How many names start with a dot.
    """

    files: tuple[str, ...]
    directories: tuple[str, ...]
    hidden: int


@dataclass(frozen=True, slots=True)
class _Place:
    """A directory to read: its path as it is reported, and where it really is."""

    shown: str
    root: str
    parts: tuple[str, ...]


def shown_path(path: str) -> str:
    """``path`` as it is reported: what hides, reorders or cannot be sent is escaped."""
    return "".join(_escaped(c) if unseen(c) or "\ud800" <= c <= "\udfff" else c for c in path)


def _escaped(character: str) -> str:
    return character.encode("unicode_escape").decode()


class _Walk:
    """One walk: the directories still to read and what was found so far."""

    def __init__(self, roots: Sequence[str], most: int, at: Callable[[str], None]) -> None:
        self.roots: Sequence[str] = roots
        self.most: int = most
        self.at: Callable[[str], None] = at
        self.files: list[str] = []
        self.hidden: int = 0
        self.entries: int = 0
        self.seen: set[tuple[int, int]] = set()
        self.real: list[_Place] = []
        self.linked: list[_Place] = []

    def read(self, place: _Place) -> None:
        """Sort the entries of a directory into files and directories.

        Raises:
            OSError: It is not there, or a link is on the way to it.
            Validation: The walk has too many entries.
        """
        held = open_directory(place.root, place.parts)
        try:
            info = os.fstat(held)
            if (info.st_dev, info.st_ino) in self.seen:
                return
            names = self._names(held)
            self.seen.add((info.st_dev, info.st_ino))
            self.at("listed")
            for name in sorted(names):
                self._sort(place, name, held)
        finally:
            os.close(held)

    def _names(self, held: int) -> list[str]:
        names: list[str] = []
        with os.scandir(held) as entries:
            for entry in entries:
                self.entries += 1
                if self.entries > self.most:
                    message = f"The directory has more than {self.most} entries."
                    hint = "Name the files in paths, or give a smaller directory."
                    raise Validation(message, hint=hint)
                names.append(entry.name)
        return names

    def _sort(self, place: _Place, name: str, held: int) -> None:
        if dotted(name):
            self.hidden += 1
            return
        shown = str(PurePath(place.shown, name))
        try:
            mode = os.stat(name, dir_fd=held, follow_symlinks=False).st_mode
        except OSError:  # gone: reading it says so
            mode = 0
        if stat.S_ISDIR(mode):
            self.real.append(_Place(shown, place.root, (*place.parts, name)))
        elif stat.S_ISLNK(mode) and (target := self._directory(shown)):
            self.linked.append(target)
        else:
            self.files.append(shown)

    def _directory(self, shown: str) -> _Place | None:
        """Where a link leads, if that is a directory inside the allowlist."""
        try:
            found = inside(os.path.realpath(shown), self.roots)
            if found is None or unreadable(found[1]):
                return None
            self.at("followed")
            os.close(open_directory(*found))
        except OSError, ValueError:
            return None
        return _Place(shown, *found)


def list_allowed(  # noqa: PLR0913 -- the seams of the tests, all defaulted
    directory: str,
    roots: Sequence[str],
    *,
    recursive: bool = False,
    most: int = MAX_ENTRIES,
    secure: bool = SECURE,
    at: Callable[[str], None] = lambda _stage: None,
) -> Listing:
    """What is in ``directory``, which must lie inside ``roots``.

    ``at`` is a test seam, called with the stage just reached: ``resolved`` (the path is approved),
    ``listed`` (the names of a directory were read), ``followed`` (a link was resolved) and
    ``looked`` (a directory is fully sorted).

    Raises:
        Validation: No allowlist, a relative path, a platform without safe opening, or more than
            ``most`` entries.
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
    walk = _Walk(roots, most, at)
    try:
        if ".." in PurePath(directory).parts:
            raise Refused  # noqa: TRY301 -- one answer for every refusal
        found = inside(os.path.realpath(directory), roots)
        if found is None or unreadable(found[1]):
            raise Refused  # noqa: TRY301 -- one answer for every refusal
        at("resolved")
        walk.read(_Place(str(PurePath(directory)), *found))
    except OSError, ValueError, Refused:  # ValueError: a path that cannot be encoded
        message = "No directory was found at that path inside the allowed directories."
        hint = f"Give the absolute path of a directory inside one of: {', '.join(roots)}."
        raise NotFound(message, hint=hint) from None
    at("looked")
    while recursive and (walk.real or walk.linked):
        place = walk.real.pop(0) if walk.real else walk.linked.pop(0)
        try:
            walk.read(place)
        except OSError:  # no directory any more: reading it as a file gives the one refusal
            walk.files.append(place.shown)
    left = [place.shown for place in (*walk.real, *walk.linked)]
    return Listing(tuple(sorted(walk.files, key=_order)), tuple(sorted(left)), walk.hidden)


def _order(path: str) -> tuple[str, str]:
    return shown_path(path), path
