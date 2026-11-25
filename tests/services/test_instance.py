"""InstanceService: the overview use case and the startup probe."""

import asyncio
import time
from typing import override

import pytest
from fakes.paperless import FakePaperless, seeded
from harness import FailingReader, HangingReader, admin

from mcp_server_paperless.domain.errors import (
    AuthenticationFailed,
    NotFound,
    PaperlessMcpError,
    UnsupportedVersion,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
)
from mcp_server_paperless.domain.instance import UpdateCheck
from mcp_server_paperless.services.instance import InstanceService, SystemReportService

pytestmark = pytest.mark.anyio


async def test_overview_is_what_the_reader_reports() -> None:
    reader = admin()

    assert await InstanceService(reader).overview() == await reader.get_overview()


async def test_the_probe_returns_the_overview_of_a_reachable_paperless() -> None:
    overview = await InstanceService(admin()).probe(timeout=1.0)

    assert overview is not None
    assert overview.paperless_version == "3.5.1"


@pytest.mark.decision("decision-2")
@pytest.mark.parametrize(
    "error",
    [
        AuthenticationFailed("bad token"),
        UnsupportedVersion("not API 10"),
        NotFound("no statistics: not a Paperless URL"),
        PaperlessMcpError("unexpected response"),
    ],
    ids=type,
)
async def test_the_probe_fails_fast_on_what_a_retry_cannot_fix(error: PaperlessMcpError) -> None:
    with pytest.raises(type(error)) as caught:
        _ = await InstanceService(FailingReader(error)).probe(timeout=1.0)

    assert caught.value is error


@pytest.mark.decision("decision-2")
@pytest.mark.parametrize("error", [UpstreamUnreachable("down"), UpstreamTimeout("slow")], ids=type)
async def test_the_probe_tolerates_an_unavailable_paperless(error: PaperlessMcpError) -> None:
    assert await InstanceService(FailingReader(error)).probe(timeout=1.0) is None


@pytest.mark.decision("decision-2")
async def test_the_probe_gives_up_after_its_timeout() -> None:
    started = time.monotonic()

    assert await InstanceService(HangingReader()).probe(timeout=0.05) is None
    assert time.monotonic() - started < 1.0


@pytest.mark.decision("decision-105")
async def test_the_report_asks_for_an_update_only_when_it_is_told_to() -> None:
    class Asked(FakePaperless):
        asked: int = 0

        @override
        async def check_for_update(self) -> UpdateCheck:
            self.asked += 1
            return await super().check_for_update()

    fake = Asked(seeded(), user="admin")
    service = SystemReportService(fake)

    plain = await service.report()
    checked = await service.report(check_for_update=True)

    assert (plain.update, fake.asked) == (None, 1)
    assert checked.update == UpdateCheck(newest_release="v3.5.1", update_available=False)
    assert checked.statistics == plain.statistics


@pytest.mark.decision("decision-105")
@pytest.mark.parametrize("error", [UpstreamTimeout("slow"), UpstreamUnavailable("down")], ids=type)
async def test_a_check_that_fails_is_unknown_and_the_report_stays(error: PaperlessMcpError) -> None:
    class Failing(FakePaperless):
        @override
        async def check_for_update(self) -> UpdateCheck:
            raise error

    report = await SystemReportService(Failing(seeded(), user="admin")).report(
        check_for_update=True
    )

    assert report.update == UpdateCheck(newest_release=None, update_available=None)
    assert report.statistics.documents == 2


@pytest.mark.decision("decision-105")
async def test_a_check_is_given_up_after_its_own_bound() -> None:
    class Hanging(FakePaperless):
        @override
        async def check_for_update(self) -> UpdateCheck:
            _ = await asyncio.Event().wait()
            raise AssertionError

    service = SystemReportService(Hanging(seeded(), user="admin"), update_seconds=0.01)

    report = await service.report(check_for_update=True)

    assert report.update == UpdateCheck(newest_release=None, update_available=None)
