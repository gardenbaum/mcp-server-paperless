"""Where an upload's bytes and name come from, checked before anything reaches Paperless.

Two sources: base64 content (any caller) and a file inside an allowlist of directories (stdio
only; decision-7). A path is the dangerous one: the server reads it on the caller's behalf, so
the rules below make it read nothing but a regular file inside a directory the operator named.

- The path is resolved with ``realpath`` (symlinks, ``..``, loops) and must then lie inside one
  of the allowlisted directories, which are resolved the same way.
- The file is then opened *relative to the directory*, one component at a time, each with
  ``O_NOFOLLOW`` (and directories with ``O_DIRECTORY``): if anything is swapped for a symlink
  between the check and the open, the open fails instead of following it. Opening by the resolved
  string alone would leave that gap.
- Only a regular file is read, decided on the descriptor that was opened (``fstat``), after a
  ``stat`` without opening so a pipe cannot block the open. A device, pipe, socket, directory or
  symlink is refused. At most ``max_bytes`` + 1 are read, so a file that grows is still bounded.
- A file with more than one hard link is refused: a link can name a file outside the allowlist,
  and there is no telling where it came from. Names below the allowlist that start with a dot
  (``.ssh``, ``.env``, ``.git``) are refused: credentials live there. So are names that start
  with a dot behind format characters (HFS+ ignores some) and names with control or line-break
  characters.
- A path with a ``..`` component is refused before anything is resolved. Resolving it first would
  answer differently depending on what exists outside the allowlist.
- A path outside the allowlist, a missing one, an unreadable one and all of the above get the
  same answer, so the tool is no oracle for what exists on the host.

Known limit: the allowlisted directory itself is opened by its resolved name, so someone who may
rewrite a parent of that directory can still swap it between the check and the open. The
directory belongs to the operator who allows it; everything below it is walked without
following links. A file whose extension does not match its content keeps its name: Paperless
decides the type from the content.
"""

import base64
import os
import stat
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import MAX_UPLOAD_LIMIT
from mcp_server_paperless.domain.errors import NotFound, Validation
from mcp_server_paperless.services.allowed_paths import (
    SECURE,
    Refused,
    inside,
    open_directory,
    unreadable,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

KIB: Final = 1024
MIB: Final = KIB * KIB
READ_CHUNK: Final = 64 * KIB


def size_text(count: int) -> str:
    """A byte count for messages: 10 MiB, 512 KiB or 100 bytes."""
    if count % MIB == 0:
        return f"{count // MIB} MiB"
    if count % KIB == 0:
        return f"{count // KIB} KiB"
    return f"{count} bytes"


def _too_large(max_bytes: int) -> Validation:
    message = f"The file is larger than the limit of {size_text(max_bytes)}."
    hint = "Upload a smaller file."
    if max_bytes < MAX_UPLOAD_LIMIT:
        hint = "Upload a smaller file; the operator can raise PAPERLESS_MCP_MAX_UPLOAD_BYTES."
    return Validation(message, hint=hint)


def decode_base64(content: str, *, max_bytes: int) -> bytes:
    """The bytes of standard base64 ``content``, at most ``max_bytes`` of them.

    The length is checked before decoding, so an oversized payload costs a comparison, not a
    decode (decoding is linear, about a gigabyte per second, but allocates the whole result).

    Raises:
        Validation: Too large, empty, or not standard base64 (no whitespace, prefix or padding
            problems).
    """
    if len(content) > 4 * -(-max_bytes // 3):
        raise _too_large(max_bytes)
    try:
        data = base64.b64decode(content, validate=True)
    except ValueError:  # binascii.Error, or characters outside ASCII
        message = "The content is not valid base64."
        hint = "Send standard base64 (RFC 4648) on one line: no line breaks, no data: prefix."
        raise Validation(message, hint=hint) from None
    if not data:
        message = "The file is empty."
        raise Validation(message, hint="Send the file's bytes, base64-encoded.")
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    return data


def _outside(roots: Sequence[str]) -> NotFound:
    listing = ", ".join(roots)
    message = "No file was found at that path inside the allowed directories."
    return NotFound(
        message, hint=f"Give the absolute path of a regular file inside one of: {listing}."
    )


def _read(fd: int, max_bytes: int) -> bytes:
    """At most ``max_bytes`` + 1 bytes from ``fd``."""
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = os.read(fd, min(READ_CHUNK, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _open_regular(
    root: str, parts: tuple[str, ...], max_bytes: int, at: Callable[[str], None]
) -> bytes:
    """Walk ``parts`` below ``root`` without following a symlink and read the regular file."""
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    held = open_directory(root, parts[:-1])
    try:
        name = parts[-1]
        # Decided without opening: opening a pipe would wait for a writer.
        if not stat.S_ISREG(os.stat(name, dir_fd=held, follow_symlinks=False).st_mode):
            raise Refused
        at("checked")
        fd = os.open(name, flags | os.O_NONBLOCK, dir_fd=held)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise Refused
            if info.st_size > max_bytes:
                raise _too_large(max_bytes)
            at("opened")
            return _read(fd, max_bytes)
        finally:
            os.close(fd)
    finally:
        os.close(held)


def read_allowed(
    path: str,
    roots: Sequence[str],
    *,
    max_bytes: int,
    secure: bool = SECURE,
    at: Callable[[str], None] = lambda _stage: None,
) -> tuple[str, bytes]:
    """The name and bytes of the regular file at ``path``, which must lie inside ``roots``.

    ``at`` is a test seam, called with the stage just reached: ``resolved`` (the path is approved),
    ``checked`` (the file was seen to be a regular one) and ``opened`` (it was opened and
    measured). Tests change the file system there, to prove the later steps do not trust the
    earlier ones.

    Raises:
        Validation: No allowlist, a relative path, a platform without safe opening, an empty
            file or one over ``max_bytes``.
        NotFound: The path is outside the allowlist, does not exist, or is not a regular file.
    """
    if not roots:
        message = "Uploading by path is not enabled."
        hint = "Send content_base64; the operator enables paths with PAPERLESS_MCP_UPLOAD_DIRS."
        raise Validation(message, hint=hint)
    if "\x00" in path:
        message = "The path contains a NUL byte."
        raise Validation(message, hint="Give the plain path of the file.")
    if not Path(path).is_absolute():
        message = "The path must be absolute."
        raise Validation(message, hint="Give the full path starting with / (or a drive).")
    if not secure:
        message = "Uploading by path is not supported on this platform."
        raise Validation(message, hint="Send the file as content_base64.")
    if ".." in PurePath(path).parts:
        raise _outside(roots)
    try:
        resolved = os.path.realpath(path)
        found = inside(resolved, roots)
        if found is None or not found[1] or unreadable(found[1]):
            raise Refused  # noqa: TRY301 -- one answer for every refusal
        at("resolved")
        data = _open_regular(found[0], found[1], max_bytes, at)
    except OSError, ValueError, Refused:  # ValueError: a path that cannot be encoded
        raise _outside(roots) from None
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    if not data:
        message = "The file is empty."
        raise Validation(message, hint="Upload a file that has content.")
    return PurePath(path).name, data
