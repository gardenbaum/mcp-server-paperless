"""Paperless behind something that limits the rate of requests (HTTP 429) or loses answers.

Paperless itself limits only its sign-in, a proxy in front of it everything. From `begin` on every
request is answered 429, and what is asked after that is noted. A request whose answer is lost was
carried out.
"""

import inspect
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, cast, override

import httpx2

from mcp_server_paperless.domain.errors import UpstreamTimeout, UpstreamUnavailable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class RateLimit:
    """Whether the limit has begun, and what was asked since then.

    Attributes:
        after: The limit begins by itself once a request of this kind ("POST /api/...") was
            answered.
        lost: The requests, or the port methods of the fake, whose answers are lost.
    """

    began: bool = False
    after: str = ""
    lost: frozenset[str] = frozenset()
    asked: list[str] = field(default_factory=list[str])

    async def begin(self, *_args: object) -> None:
        self.began = True

    def refuses(self, what: str) -> bool:
        if self.began:
            self.asked.append(what)
        return self.began


class LimitingTransport(httpx2.AsyncHTTPTransport):
    """The way to a live Paperless, with the limit on it."""

    def __init__(self, limit: RateLimit) -> None:
        super().__init__()
        self._limit: RateLimit = limit

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        what = f"{request.method} {request.url.path}"
        if self._limit.refuses(what):
            return httpx2.Response(429, json={"detail": "Request was throttled."}, request=request)
        response = await super().handle_async_request(request)
        if what == self._limit.after:
            await self._limit.begin()
        if what in self._limit.lost:
            await response.aclose()
            message = "The answer was lost on the way."
            raise httpx2.ReadTimeout(message, request=request)
        return response


class _Limited:
    def __init__(self, port: object, limit: RateLimit) -> None:
        self._port: object = port
        self._limit: RateLimit = limit

    def __getattr__(self, name: str) -> object:
        real = cast("Callable[..., Awaitable[object]]", getattr(self._port, name))
        if not inspect.iscoroutinefunction(real):
            return real

        async def call(*args: object, **kwargs: object) -> object:
            if self._limit.refuses(name):
                message = "Paperless is temporarily unavailable (HTTP 429)."
                raise UpstreamUnavailable(message, status=429)
            answer = await real(*args, **kwargs)
            if name in self._limit.lost:
                message = "Paperless did not respond in time."
                raise UpstreamTimeout(message)
            return answer

        return call


def limited_port[P](port: P, limit: RateLimit) -> P:
    """``port`` with the limit on every call: the fake's way."""
    return cast("P", _Limited(port, limit))


def limited[P](ports: P, limit: RateLimit) -> P:
    """Ports (a dataclass of them) with the limit on every call."""
    given = [f.name for f in fields(cast("type", type(ports)))]
    wrapped = {name: limited_port(cast("object", getattr(ports, name)), limit) for name in given}
    return cast("Callable[..., P]", type(ports))(**wrapped)
