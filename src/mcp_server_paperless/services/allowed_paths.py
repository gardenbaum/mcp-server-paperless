"""Paths inside the directories the operator allowed: found, walked and named safely.

Shared by reading an upload (``upload_source``) and saving a download (``file_sink``); the rules
and their limits are explained there.
"""

import os
import unicodedata
from pathlib import PurePath
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import Validation

if TYPE_CHECKING:
    from collections.abc import Sequence

# Longest file name Paperless and common file systems take, in UTF-8 bytes.
MAX_NAME_BYTES: Final = 255
# Unicode categories no file name may hold: controls, format characters (bidirectional
# overrides, zero-width and byte order marks) and line and paragraph separators.
FORMAT: Final = "Cf"
FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", FORMAT, "Zl", "Zp"})
# `dir_fd` and `O_NOFOLLOW` are what make the walk safe; Windows has neither.
SECURE: Final = os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")


class Refused(Exception):  # noqa: N818 -- internal signal, turned into one client answer
    """The path leads to something this server does not touch."""


def clean_filename(name: str) -> str:
    """The name without directories; everything else (non-ASCII too) is kept.

    Raises:
        Validation: Nothing usable is left, or the name has control characters or is too long.
    """
    last = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    problem = None
    if last in {"", ".", ".."}:
        problem = "is empty or only dots"
    elif unseen(last):
        problem = "has control characters"
    elif len(last.encode()) > MAX_NAME_BYTES:
        problem = f"is longer than {MAX_NAME_BYTES} bytes"
    if problem:
        message = f"The file name {problem}."
        raise Validation(message, hint="Give the file a plain name such as invoice.pdf.")
    return last


def unseen(name: str) -> bool:
    """Whether a name holds characters that are not shown: controls, format characters and breaks.

    HFS+ ignores some of them, so such a name can be another one's: U+200C before ``.git`` is
    ``.git`` there.
    """
    return any(unicodedata.category(c) in FORBIDDEN_CATEGORIES for c in name)


def unreadable(parts: Sequence[str]) -> bool:
    """Whether a path below an allowed directory is not read: credentials live in dot names.

    A dot name counts as one behind format characters, too (see ``unseen``); elsewhere in a name
    they are fine (the joiner in an emoji), other characters that are not shown never are.
    """
    plain = [_plain(part) for part in parts]
    return any(part.startswith(".") or unseen(part) for part in plain)


def dotted(name: str) -> bool:
    """Whether a name starts with a dot, also behind format characters."""
    return _plain(name).startswith(".")


def _plain(name: str) -> str:
    return "".join(c for c in name if unicodedata.category(c) != FORMAT)


def unwritable(parts: Sequence[str]) -> bool:
    """Whether nothing is saved below a path: dot names and everything that is not shown."""
    return any(part.startswith(".") or unseen(part) for part in parts)


def inside(resolved: str, roots: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    """The allowlisted directory holding ``resolved`` and the path components below it."""
    return inside_real(resolved, [os.path.realpath(root) for root in roots])


def inside_real(resolved: str, roots: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    """Like ``inside``, for directories that are resolved already."""
    for real in roots:
        try:
            relative = PurePath(resolved).relative_to(real).parts
        except ValueError:
            continue
        return real, relative
    return None


def open_directory(root: str, parts: Sequence[str]) -> int:
    """A descriptor of the directory ``parts`` below ``root``, reached without a symlink."""
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
    held = os.open(root, flags)
    for part in parts:
        try:
            deeper = os.open(part, flags | os.O_NOFOLLOW, dir_fd=held)
        finally:
            os.close(held)
        held = deeper
    return held
