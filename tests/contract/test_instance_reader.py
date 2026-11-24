"""Contract of InstanceReader: the same assertions against the fake and a live Paperless."""

from typing import TYPE_CHECKING, cast

import pytest
from e2e.helpers import SEEDED_OBJECTS
from fastmcp import Client
from guide_oracle import COMBINATIONS, Seen, check
from harness import Recorded, serve

if TYPE_CHECKING:
    from contract.conftest import Backend
    from mcp_server_paperless.domain.instance import InstanceStatistics

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]


def counts(statistics: InstanceStatistics) -> dict[str, int]:
    """The statistics by the names of the seed manifest (an unknown inbox count is none)."""
    return {
        "documents": statistics.documents,
        "inbox_documents": statistics.inbox_documents or 0,
        "tags": statistics.tags,
        "correspondents": statistics.correspondents,
        "document_types": statistics.document_types,
        "storage_paths": statistics.storage_paths,
    }


@pytest.mark.contract("InstanceReader.get_overview")
async def test_overview_reports_versions_and_the_current_user(backend: Backend) -> None:
    overview = await backend.admin.instance.get_overview()

    assert (overview.paperless_version, overview.api_version) == (backend.expected_version, 10)
    assert overview.current_user is not None
    assert (overview.current_user.username, overview.current_user.is_superuser) == ("admin", True)
    assert overview.ai_enabled is False


@pytest.mark.contract("InstanceReader.get_overview")
async def test_a_superuser_sees_the_private_objects_too_and_the_system_status(
    backend: Backend,
) -> None:
    admin = await backend.admin.instance.get_overview()
    reader = await backend.reader.instance.get_overview()

    hidden = {
        name: count - counts(reader.statistics)[name]
        for name, count in counts(admin.statistics).items()
    }
    assert all(hidden[name] >= private for name, (_, private) in SEEDED_OBJECTS.items()), hidden
    status = admin.system_status
    assert status is not None
    assert (status.database.status, status.redis.status) == ("OK", "OK")
    assert status.llm_index.status == "DISABLED"
    assert 0 < status.storage_available_bytes <= status.storage_total_bytes


@pytest.mark.contract("InstanceReader.get_overview")
async def test_a_restricted_user_sees_shared_objects_and_no_system_status(
    backend: Backend,
) -> None:
    overview = await backend.reader.instance.get_overview()

    seen = counts(overview.statistics)
    assert all(seen[name] >= shared for name, (shared, _) in SEEDED_OBJECTS.items()), seen
    assert overview.system_status is None
    assert overview.current_user is not None
    assert (overview.current_user.username, overview.current_user.is_superuser) == (
        "e2e-reader",
        False,
    )
    assert overview.paperless_version == backend.expected_version


@pytest.mark.contract("InstanceReader.get_overview")
async def test_a_user_without_the_ui_settings_permission_gets_no_ai_flag(backend: Backend) -> None:
    overview = await backend.restricted(frozenset()).instance.get_overview()

    assert (overview.ai_enabled, overview.current_user) == (None, None)


@pytest.mark.contract("InstanceReader.get_overview")
@pytest.mark.parametrize("who", ["admin", "reader", "blind", "no-settings"])
async def test_the_guide_follows_what_the_reader_says_about_ai(backend: Backend, who: str) -> None:
    """The capability the guide reads is the one each kind of user gets: false, or unknown.

    The reader answers once; every combination of selection, disabled tools and read-only mode is
    then started on that answer.
    """
    ports = backend.restricted(frozenset()) if who == "no-settings" else getattr(backend, who)
    overview = await ports.instance.get_overview()
    capability = "unknown" if who == "no-settings" else "missing"
    assert (overview.ai_enabled is None) is (who == "no-settings")

    ceilings: dict[tuple[str, bool], Seen | None] = {}
    for combination in COMBINATIONS:
        await check(
            lambda: Recorded(overview),
            capability=capability,
            combination=combination,
            ceilings=ceilings,
        )


@pytest.mark.decision("decision-57")
@pytest.mark.contract("InstanceReader.get_overview")
@pytest.mark.parametrize("version", ["3.4.2", "3.5.0", None, "99.0.0", "unknown"])
async def test_only_an_older_release_is_called_too_old(
    backend: Backend, version: str | None
) -> None:
    """None is the release the backend runs: the minimum, or a newer one in the weekly run."""
    overview = await backend.admin.instance.get_overview()
    if version is not None:
        overview = overview.model_copy(update={"paperless_version": version})

    async with Client(serve(Recorded(overview))) as client:
        result = await client.call_tool("get_instance_overview")

    assert result.structured_content is not None
    output = cast("dict[str, str | None]", result.structured_content)
    assert output["paperless_version"] == (version or backend.expected_version)
    notice = f"{version} is older than the minimum release 3.5.1: Paperless must be updated"
    if version in {"3.4.2", "3.5.0"}:
        assert (output["too_old"] or "").startswith(notice)
    else:
        assert output["too_old"] is None
