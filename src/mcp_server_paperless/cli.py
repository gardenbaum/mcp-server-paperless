"""Command-line entrypoints: ``paperless-mcp`` (stdio) and ``paperless-mcp-http`` (HTTP).

Configuration errors and a Paperless that rejects the token or API version end the process
with exit code 1 and one line on stderr, without a traceback.
"""

import sys
from typing import TYPE_CHECKING, NoReturn

from pydantic import ValidationError
from pydantic_settings import BaseSettings

from mcp_server_paperless.app import create_app, run_server, uvicorn_server
from mcp_server_paperless.domain.errors import PaperlessMcpError
from mcp_server_paperless.observability import configure_logging
from mcp_server_paperless.server import create_server
from mcp_server_paperless.settings import (
    HttpSettings,
    OidcSettings,
    PaperlessSettings,
    ServerSettings,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import uvicorn


def _fail(message: str) -> NoReturn:
    print(f"paperless-mcp: {message}", file=sys.stderr)  # noqa: T201 -- the user-facing exit message
    raise SystemExit(1)


def _load[S: BaseSettings](kind: type[S], problems: list[str]) -> S | None:
    """The settings from the environment, or None with one line per invalid variable."""
    try:
        return kind.model_validate({})
    except ValidationError as error:
        prefix = kind.model_config.get("env_prefix") or ""
        # Only the variable and the reason: the input may be a token.
        problems.extend(
            f"{prefix}{'_'.join(map(str, problem['loc'])).upper()}: {problem['msg']}"
            for problem in error.errors()
        )
        return None


def main() -> None:
    """Run the server over stdio."""
    problems: list[str] = []
    paperless = _load(PaperlessSettings, problems)
    settings = _load(ServerSettings, problems)
    if paperless is None or settings is None:
        _fail("invalid configuration: " + "; ".join(problems))
    configure_logging(settings.log_level)
    try:
        # No banner: it is noise on stderr and would check PyPI for updates (decision-7).
        create_server(paperless, settings, stdio=True).run(transport="stdio", show_banner=False)
    except PaperlessMcpError as error:
        _fail(error.describe())


def main_http(run: Callable[[uvicorn.Server], bool] = run_server) -> None:
    """Run the server over Streamable HTTP; refuses to start without authentication.

    Authentication is static bearer tokens, the optional OIDC layer (``PAPERLESS_MCP_OIDC_*``),
    or both (decision-31).

    Args:
        run: Serves the uvicorn server and says whether it came up; tests pass a fake.
    """
    problems: list[str] = []
    paperless = _load(PaperlessSettings, problems)
    settings = _load(ServerSettings, problems)
    http = _load(HttpSettings, problems)
    oidc = _load(OidcSettings, problems)
    if paperless is None or settings is None or http is None or oidc is None:
        _fail("invalid configuration: " + "; ".join(problems))
    configure_logging(settings.log_level)
    try:
        app = create_app(paperless, settings, http, oidc=oidc)
    except PaperlessMcpError as error:
        _fail(error.describe())
    if not run(uvicorn_server(app, http)):
        _fail("the HTTP server did not start; see the log above")
