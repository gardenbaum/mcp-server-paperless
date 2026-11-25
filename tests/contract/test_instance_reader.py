"""Contract of InstanceReader: the same assertions against the fake and a live Paperless."""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from e2e.helpers import SEEDED_OBJECTS
from fakes.accounts import PRIVATE_ASN
from fastmcp import Client
from guide_oracle import COMBINATIONS, Seen, check
from harness import Recorded, serve

from contract.conftest import SCRATCH_TAG
from mcp_server_paperless.domain.documents import DocumentPatch
from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.domain.instance import MINIMUM

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
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


@pytest.mark.decision("decision-101")
@pytest.mark.contract("InstanceReader.get_overview")
async def test_the_next_number_counts_hidden_documents_but_not_the_trash(backend: Backend) -> None:
    async def following(ports: Ports) -> int | None:
        return (await ports.instance.get_overview()).next_asn

    admin = backend.admin
    document = backend.new_document()
    before = await following(admin)
    assert before is not None
    assert before > PRIVATE_ASN
    # A document of another user is as good as any other: the number is one for the instance.
    _ = await admin.document_writes.update_document(
        document, DocumentPatch(archive_serial_number=before + 5)
    )
    seen = [await following(ports) for ports in (admin, backend.reader)]
    await admin.document_writes.delete_document(document)
    after = await following(admin)
    blind = await following(backend.blind)
    other = backend.new_document()
    with pytest.raises(Validation, match="Archive Serial Number already exists in the trash"):
        _ = await admin.document_writes.update_document(
            other, DocumentPatch(archive_serial_number=before + 5)
        )

    assert seen == [before + 6, before + 6]
    assert (after, blind) == (before, None)


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

    ceilings: dict[tuple[object, ...], Seen | None] = {}
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
    notice = f"{version} is older than the minimum release {MINIMUM}: Paperless must be updated"
    if version in {"3.4.2", "3.5.0"}:
        assert (output["too_old"] or "").startswith(notice)
    else:
        assert output["too_old"] is None


@pytest.mark.decision("decision-97")
@pytest.mark.contract("InstanceReader.get_overview")
async def test_a_mixed_selection_reads_everywhere_and_writes_only_where_it_may(
    backend: Backend,
) -> None:
    """`core,mail:read,workflows`: what is offered works on Paperless, and mail is left alone."""
    ports = backend.admin
    server = serve(
        ports.instance,
        toolsets=["core", "mail:read", "workflows"],
        taxonomy=ports.taxonomy,
        taxonomy_writes=ports.taxonomy_writes,
        mail=ports.mail,
        mail_writes=ports.mail_writes,
        workflows=ports.workflows,
        workflow_writes=ports.workflow_writes,
    )
    rules = await ports.mail.list_mail_rules()
    accounts = await ports.mail.list_mail_accounts()
    name = f"{SCRATCH_TAG}{uuid.uuid4().hex}"
    account = {
        "name": name,
        "imap_server": "127.0.0.1",
        "imap_port": 1,
        "username": "unset",
        "password": "unset",
    }

    async with Client(server) as client:
        overview = await client.call_tool("get_instance_overview")
        for tool in ("list_mail_accounts", "list_mail_rules", "list_workflows"):
            assert not (await client.call_tool(tool)).is_error, tool
        created = await client.call_tool("create_taxonomy_item", {"kind": "tag", "name": name})
        refused = [
            await client.call_tool(tool, arguments, raise_on_error=False)
            for tool, arguments in (
                ("create_mail_account", account),
                ("delete_mail_rule", {"rule_id": 1, "confirm": True}),
                ("process_mail_account", {"account_id": 1}),
            )
        ]

    assert overview.structured_content is not None
    assert overview.structured_content["toolsets"]["on"] == [
        "core",
        "mail (read-only)",
        "workflows",
    ]
    assert not created.is_error
    assert name in {tag.name for tag in await ports.taxonomy.list_taxonomy("tag")}
    assert all(result.is_error and "Unknown tool" in str(result.content) for result in refused)
    assert await ports.mail.list_mail_rules() == rules
    assert await ports.mail.list_mail_accounts() == accounts


REPORT = "SystemReportReader.get_system_report"


@pytest.mark.contract(REPORT)
@pytest.mark.decision("decision-95")
async def test_the_report_of_a_superuser_says_more_than_the_overview(backend: Backend) -> None:
    report = await backend.admin.reports.get_system_report()
    overview = await backend.admin.instance.get_overview()

    statistics, status = report.statistics, report.status
    assert statistics.documents >= sum(SEEDED_OBJECTS["documents"])
    assert sum(kind.documents for kind in statistics.file_types) == statistics.documents
    assert statistics.characters > 0
    assert statistics.inbox_tags
    assert status is not None
    assert status.paperless_version == backend.expected_version
    assert overview.system_status is not None
    assert status.install_type == overview.system_status.install_type
    assert (status.database.type, status.database.unapplied_migrations) == ("sqlite", ())
    assert status.database.latest_migration
    assert status.redis.address
    assert status.server_os
    assert status.search_index.status == "OK"
    assert status.tasks.days == 30
    assert status.tasks.total >= status.tasks.success + status.tasks.failure


@pytest.mark.contract(REPORT)
@pytest.mark.decision("decision-95")
async def test_the_report_follows_the_rights_of_the_user(backend: Backend) -> None:
    monitor = backend.restricted(frozenset({"view_system_monitoring"}))
    everything = await backend.admin.reports.get_system_report()

    seen = await monitor.reports.get_system_report()
    restricted = await backend.reader.reports.get_system_report()

    assert seen.status is not None
    assert restricted.status is None
    assert restricted.statistics.documents < everything.statistics.documents
    assert seen.statistics.documents < everything.statistics.documents


@pytest.mark.contract("SystemReportReader.check_for_update")
@pytest.mark.decision("decision-105")
async def test_the_check_for_an_update_says_both_or_neither(backend: Backend) -> None:
    # Paperless asks GitHub, which may not answer it.
    for ports in (backend.admin, backend.blind):
        found = await ports.reports.check_for_update()
        assert (found.newest_release is None) is (found.update_available is None)
        assert found.newest_release is None or found.newest_release.startswith("v3.")
