"""Instance-wide use cases: the overview tool and the startup probe."""

import asyncio
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import UpstreamUnavailable
from mcp_server_paperless.domain.instance import UpdateCheck

if TYPE_CHECKING:
    from mcp_server_paperless.domain.instance import InstanceOverview, SystemReport
    from mcp_server_paperless.domain.ports import InstanceReader, SystemReportReader


# Seconds the check for an update may take in all.
UPDATE_SECONDS: Final = 10.0


class SystemReportService:
    """What Paperless's statistics and system status tell."""

    def __init__(
        self, reader: SystemReportReader, *, update_seconds: float = UPDATE_SECONDS
    ) -> None:
        """Read through ``reader``; ``update_seconds`` bounds the check for an update."""
        self._reader: SystemReportReader = reader
        self._update_seconds: float = update_seconds

    async def report(self, *, check_for_update: bool = False) -> SystemReport:
        """Both in full, as far as the user may see them, and the newest release on request."""
        report = await self._reader.get_system_report()
        if not check_for_update:
            return report
        try:
            async with asyncio.timeout(self._update_seconds):
                update = await self._reader.check_for_update()
        except TimeoutError, UpstreamUnavailable:
            update = UpdateCheck(newest_release=None, update_available=None)
        return report.model_copy(update={"update": update})


class InstanceService:
    """What the instance is and what the current user can do there."""

    def __init__(self, reader: InstanceReader) -> None:
        """Read the instance through ``reader``."""
        self._reader: InstanceReader = reader

    async def overview(self) -> InstanceOverview:
        """Versions, statistics, system status, current user and capabilities."""
        return await self._reader.get_overview()

    async def probe(self, timeout: float) -> InstanceOverview | None:  # noqa: ASYNC109 -- the bound is the probe's contract
        """Check at startup that Paperless accepts the credential and API version 10.

        A Paperless that is down or slow may come back, so the server starts anyway and the
        tools report it. Every other error stays the same until the configuration changes.

        Returns:
            The overview, or None if Paperless did not answer within ``timeout`` seconds or is
            unavailable.

        Raises:
            PaperlessMcpError: Paperless rejected the credential or the API version, or does not
                answer like Paperless (anything but ``UpstreamUnavailable``).
        """
        try:
            async with asyncio.timeout(timeout):
                return await self._reader.get_overview()
        except TimeoutError, UpstreamUnavailable:
            return None
