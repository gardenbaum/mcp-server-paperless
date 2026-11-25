"""What a log may say of an exception: types and the place in the code, never a message or value."""

import traceback
from errno import errorcode
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Generator

_PACKAGE: Final = "mcp_server_paperless"


def error_types(error: BaseException | None) -> str:
    """The types of ``error``, of what it groups and of what it came from; never raises.

    A built-in ``OSError`` also has the name of its error code, such as ``EADDRINUSE``.
    """
    try:
        return _types(error, set())
    except Exception:  # noqa: BLE001 -- whatever an error's attributes do: the plain name
        return type(error).__name__


def _types(error: BaseException | None, seen: set[int]) -> str:
    names: list[str] = []
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        name = type(error).__name__
        if isinstance(error, BaseExceptionGroup):
            name += f"[{', '.join(_types(member, seen) for member in error.exceptions)}]"
        # Subclasses of other modules (ssl, socket) number their errors differently.
        elif isinstance(error, OSError) and type(error).__module__ == "builtins":
            code = errorcode.get(error.errno or 0)
            name += f"({code})" if code else ""
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
    """`` at module:line in function`` of the innermost frame; empty if it was never raised.

    Of the error that the others came from. If that frame is not of this package, its innermost
    one follows. Never raises.
    """
    try:
        frames = list(_frames(error, set()))
    except Exception:  # noqa: BLE001 -- whatever an error's attributes do: no place
        return ""
    if not frames:
        return ""
    own = next((frame for frame in frames if frame[0].split(".")[0] == _PACKAGE), None)
    places = dict.fromkeys(frame for frame in (frames[0], own) if frame is not None)
    return " at " + ", called from ".join(f"{m}:{line} in {f}" for m, line, f in places)


def _frames(error: BaseException | None, seen: set[int]) -> Generator[tuple[str, int, str]]:
    """The frames of ``error``, innermost first, after those of what it came from or groups."""
    if error is None or id(error) in seen:
        return
    seen.add(id(error))
    yield from _frames(error.__cause__ or error.__context__, seen)
    if isinstance(error, BaseExceptionGroup):
        for member in error.exceptions:
            yield from _frames(member, seen)
    for frame, line in reversed(list(traceback.walk_tb(error.__traceback__))):
        module = str(cast("object", frame.f_globals.get("__name__")))
        yield module, line, frame.f_code.co_name
