"""Ports on which somebody else does something just before a call: the seam is the constructor.

A step that fails because the document, the grants or the rows changed meanwhile cannot be raced
for on a live Paperless. `before` runs the change at a known place of the call instead and then
makes the real call, so what fails is what Paperless refuses.
"""

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class _Before:
    def __init__(
        self, port: object, method: str, act: Callable[..., Awaitable[None]], at: int
    ) -> None:
        self._port: object = port
        self._method: str = method
        self._act: Callable[..., Awaitable[None]] = act
        self._left: int = at

    def __getattr__(self, name: str) -> object:
        real = cast("Callable[..., Awaitable[object]]", getattr(self._port, name))
        if name != self._method:
            return real

        async def call(*args: object, **kwargs: object) -> object:
            self._left -= 1
            if self._left == 0:
                await self._act(*args)
            return await real(*args, **kwargs)

        return call


def before[P](port: P, method: str, act: Callable[..., Awaitable[None]], *, at: int = 1) -> P:
    """``port``, but its call number ``at`` of ``method`` waits for ``act(*args)`` first."""
    return cast("P", _Before(port, method, act, at))
