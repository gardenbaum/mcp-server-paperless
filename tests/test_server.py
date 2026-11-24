"""The composition root: the startup probe through the real adapter, and the server lifespan."""

import time
from typing import cast

import httpx2
import pytest
from adapter.test_instance import instance
from fastmcp import Client
from harness import (
    HangingReader,
    paperless_answering,
    paperless_settings,
    refused_url,
    serve,
    server_settings,
)

from mcp_server_paperless.domain.errors import (
    AuthenticationFailed,
    PaperlessMcpError,
    UnsupportedVersion,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy
from mcp_server_paperless.server import create_server, paperless_ports

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-2")]


@pytest.mark.parametrize(
    ("status", "error"), [(401, AuthenticationFailed), (406, UnsupportedVersion)]
)
async def test_the_server_does_not_start_when_paperless_rejects_the_token_or_api_version(
    status: int, error: type[PaperlessMcpError]
) -> None:
    with paperless_answering(status) as url:
        server = create_server(paperless_settings(url), server_settings())

        with pytest.raises(RuntimeError) as caught:
            async with Client(server):
                pass

    assert isinstance(caught.value.__cause__, error)


async def test_an_unreachable_paperless_does_not_stop_the_start() -> None:
    server = create_server(paperless_settings(refused_url()), server_settings())

    async with Client(server) as client:
        result = await client.call_tool("get_instance_overview", raise_on_error=False)

    assert result.is_error
    assert "Paperless is unreachable." in str(result.content)


async def test_a_paperless_that_does_not_answer_delays_the_start_only_up_to_the_timeout() -> None:
    started = time.monotonic()

    async with Client(serve(HangingReader(), startup_timeout=0.1)):
        pass

    assert time.monotonic() - started < 2.0


@pytest.mark.decision("decision-57")
async def test_an_old_paperless_that_comes_up_after_the_start_is_still_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    up = False
    responses = instance()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if not up:
            message = "down"
            raise httpx2.ConnectError(message, request=request)
        found = responses[request.url.path]
        return httpx2.Response(
            found.status_code,
            content=found.content,
            headers={"X-Version": "3.4.2", "X-Api-Version": "10"},
        )

    async with httpx2.AsyncClient(
        base_url="http://paperless.test", transport=httpx2.MockTransport(handler)
    ) as http:
        paperless = PaperlessClient(
            http, StaticTokenProvider("token"), retry=RetryPolicy(attempts=1), upload_timeout=1.0
        )
        server = create_server(
            paperless_settings(),
            server_settings(),
            ports=lambda _client: paperless_ports(paperless),
        )
        async with Client(server) as client:
            assert not [r for r in caplog.records if "minimum release" in r.getMessage()]
            up = True
            results = [await client.call_tool("get_instance_overview") for _ in range(2)]

    assert all(
        cast("dict[str, str]", result.structured_content)["paperless_version"].startswith(
            "3.4.2 is older than"
        )
        for result in results
    )
    assert len([r for r in caplog.records if "minimum release" in r.getMessage()]) == 1
