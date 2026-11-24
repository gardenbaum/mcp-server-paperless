"""What a log may say of an exception: types and the place in the code, never a message or value."""

import traceback
from errno import errorcode
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Generator

_PACKAGE: Final = "mcp_server_paperless"


def error_types(error: BaseException | None, seen: set[int] | None = None) -> str:
    """The types of ``error``, of what it groups and of what it came from.

    An ``OSError`` also has the name of its error code, such as ``EADDRINUSE``.
    """
    seen = set() if seen is None else seen
    names: list[str] = []
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        name = type(error).__name__
        if isinstance(error, BaseExceptionGroup):
            members = error.exceptions
            name += f"[{', '.join(error_types(member, seen) for member in members)}]"
        elif isinstance(error, OSError) and (code := errorcode.get(error.errno or 0)):
            name += f"({code})"
        names.append(name)
        error = error.__cause__ or error.__context__
    return " from ".join(names)


def members(error: BaseException | None) -> Generator[BaseException]:
    """``error``, or what it groups, at any depth."""
    if isinstance(error, BaseExceptionGroup):
        for member in error.exceptions:
            yield from members(member)
    elif error is not None:
        yield error


def error_place(error: BaseException | None) -> str:
    """`` at module:line in function`` of the innermost frame of this package, or of any.

    Of the error itself, else of the one it came from; empty if it was never raised.
    """
    frames: list[tuple[str, int, str]] = []
    seen: set[int] = set()
    waiting = list(members(error))
    while waiting:
        current = waiting.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        # Innermost first; the error that others came from is looked at after them.
        frames += reversed(
            [
                (str(cast("object", frame.f_globals.get("__name__"))), line, frame.f_code.co_name)
                for frame, line in traceback.walk_tb(current.__traceback__)
            ]
        )
        waiting += members(current.__cause__ or current.__context__)
    own = next((frame for frame in frames if frame[0].split(".")[0] == _PACKAGE), None)
    if not frames:
        return ""
    module, line, function = own or frames[0]
    return f" at {module}:{line} in {function}"
