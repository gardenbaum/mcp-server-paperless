"""What the server says about the release of a real Paperless (decision-57).

The stack runs one release; what it reports is rewritten on its answers, everything else is real.
"""

from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import EXPECTED_VERSION
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy
from mcp_server_paperless.server import create_server, paperless_ports

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-57")]

NOTICE = " is older than the minimum release 3.5.1: Paperless must be updated"


@pytest.mark.parametrize(
    ("reported", "shown", "old"),
    [
        pytest.param("3.4.2", "3.4.2", True, id="older"),
        pytest.param("3.5.0", "3.5.0", True, id="just older"),
        pytest.param(EXPECTED_VERSION, EXPECTED_VERSION, False, id="as it is"),
        pytest.param("99.0.0", "99.0.0", False, id="newer"),
        pytest.param("dev", "dev", False, id="unparsable"),
        pytest.param(None, "unknown", False, id="missing"),
    ],
)
async def test_only_an_older_release_is_reported(
    stack: Stack, caplog: pytest.LogCaptureFixture, reported: str | None, shown: str, *, old: bool
) -> None:
    async def report(response: httpx2.Response) -> None:
        assert response.headers.pop("X-Version") == EXPECTED_VERSION
        if reported is not None:
            response.headers["X-Version"] = reported

    async with httpx2.AsyncClient(
        base_url=stack.base_url, timeout=30.0, event_hooks={"response": [report]}
    ) as http:
        paperless = PaperlessClient(
            http, StaticTokenProvider(stack.admin_token), retry=RetryPolicy(), upload_timeout=60.0
        )
        server = create_server(
            paperless_settings(stack.base_url, stack.admin_token),
            server_settings(),
            ports=lambda _client: paperless_ports(paperless),
        )
        async with Client(server) as client:
            result = await client.call_tool("get_instance_overview")
            tags = await client.call_tool("list_taxonomy", {"kind": "tag"})

    assert result.structured_content is not None
    version = cast("dict[str, str]", result.structured_content)["paperless_version"]
    assert version.startswith(shown + NOTICE) if old else version == shown
    assert not tags.is_error  # it works all the same
    logged = [r.getMessage() for r in caplog.records if "minimum release" in r.getMessage()]
    assert [line.startswith(f"Paperless {shown}{NOTICE}") for line in logged] == [True] * old
