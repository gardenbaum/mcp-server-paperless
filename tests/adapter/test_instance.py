"""PaperlessInstanceReader on responses recorded from the Paperless 3.4.2 E2E stack."""

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from adapter.conftest import streaming
from mcp_server_paperless.domain.errors import (
    AuthenticationFailed,
    PaperlessMcpError,
    UpstreamUnavailable,
)
from mcp_server_paperless.domain.instance import (
    ComponentStatus,
    CurrentUser,
    DatabaseStatus,
    FileTypeCount,
    InstanceOverview,
    InstanceStatistics,
    ServiceStatus,
    StatisticsReport,
    StatusReport,
    SystemReport,
    SystemStatus,
    TaskTotals,
    TimedStatus,
)
from mcp_server_paperless.paperless.client import RetryPolicy
from mcp_server_paperless.paperless.instance import PaperlessInstanceReader

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

# How the names of the moments in the status end.
MOMENTS = ("last_modified", "last_trained", "last_run")
VERSION_HEADERS = {"X-Api-Version": "10", "X-Version": "3.4.2"}
STATISTICS: Json = {
    "documents_total": 2,
    "documents_inbox": 1,
    "inbox_tag": 4,
    "inbox_tags": [4],
    "document_file_type_counts": [{"mime_type": "application/pdf", "mime_type_count": 2}],
    "character_count": 55,
    "tag_count": 4,
    "correspondent_count": 1,
    "document_type_count": 1,
    "storage_path_count": 1,
    "current_asn": 0,
}
STATUS: Json = {
    "pngx_version": "3.4.2",
    "server_os": "Linux-6.0-x86_64-with-glibc2.41",
    "install_type": "docker",
    "storage": {"total": 53217329152, "available": 42602811392},
    "database": {
        "type": "sqlite",
        "url": "/usr/src/paperless/data/db.sqlite3",
        "status": "OK",
        "error": None,
        "migration_status": {
            "latest_migration": "paperless_mail.0001_squashed",
            "unapplied_migrations": [],
        },
    },
    "tasks": {
        "redis_url": "redis://broker:6379",
        "redis_status": "OK",
        "redis_error": None,
        "celery_status": "OK",
        "celery_url": "celery@9464bd509d18",
        "celery_error": None,
        "index_status": "OK",
        "index_last_modified": "2026-11-09T21:43:38.159239Z",
        "index_error": None,
        "classifier_status": "WARNING",
        "classifier_last_trained": None,
        "classifier_error": "No classifier training tasks found",
        "sanity_check_status": "WARNING",
        "sanity_check_last_run": None,
        "sanity_check_error": "No sanity check tasks found",
        "llmindex_status": "DISABLED",
        "llmindex_last_modified": None,
        "llmindex_error": None,
        "summary": {
            "days": 30,
            "total_count": 3,
            "pending_count": 0,
            "success_count": 3,
            "failure_count": 0,
        },
    },
}
SETTINGS: Json = {
    "update_checking": {"backend_setting": "default"},
    "trash_delay": 30,
    "version": "3.4.2",
    "app_title": None,
    "app_logo": None,
    "auditlog_enabled": True,
    "remote_ocr": {"configured": False, "mode": "always"},
    "email_enabled": False,
    "ai_enabled": False,
}
UI_SETTINGS: Json = {
    "user": {"id": 2, "username": "admin", "is_staff": True, "is_superuser": True, "groups": []},
    "settings": SETTINGS,
    "permissions": ["view_documenttype", "change_workflow", "add_userobjectpermission"],
}
# What 3.4.2 answers a user without the permission (SystemStatusView, UiSettingsView).
STATUS_FORBIDDEN = httpx2.Response(
    403, text="Insufficient permissions", headers={"Content-Type": "text/html; charset=utf-8"}
)
UI_SETTINGS_FORBIDDEN = httpx2.Response(
    403, json={"detail": "You do not have permission to perform this action."}
)


def instance(**overrides: httpx2.Response) -> dict[str, httpx2.Response]:
    """Responses by path: the admin's recorded ones, with `overrides` (path without slashes)."""
    responses = {
        "/api/statistics/": httpx2.Response(200, json=STATISTICS),
        "/api/status/": httpx2.Response(200, json=STATUS),
        "/api/ui_settings/": httpx2.Response(200, json=UI_SETTINGS),
    }
    responses.update({f"/api/{name}/": response for name, response in overrides.items()})
    return responses


def reader(
    make_client: ClientFactory, responses: dict[str, httpx2.Response]
) -> PaperlessInstanceReader:
    def handler(request: httpx2.Request) -> httpx2.Response:
        response = responses[request.url.path]
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers={**VERSION_HEADERS, **dict(response.headers)},
        )

    return PaperlessInstanceReader(make_client(handler, retry=RetryPolicy(attempts=1)))


async def test_maps_the_overview_of_a_superuser(make_client: ClientFactory) -> None:
    overview = await reader(make_client, instance()).get_overview()

    assert overview == InstanceOverview(
        paperless_version="3.4.2",
        api_version=10,
        statistics=InstanceStatistics(
            documents=2,
            inbox_documents=1,
            tags=4,
            correspondents=1,
            document_types=1,
            storage_paths=1,
        ),
        system_status=SystemStatus(
            install_type="docker",
            storage_total_bytes=53217329152,
            storage_available_bytes=42602811392,
            database=ComponentStatus(status="OK", error=None),
            redis=ComponentStatus(status="OK", error=None),
            celery=ComponentStatus(status="OK", error=None),
            search_index=ComponentStatus(status="OK", error=None),
            classifier=ComponentStatus(
                status="WARNING", error="No classifier training tasks found"
            ),
            sanity_check=ComponentStatus(status="WARNING", error="No sanity check tasks found"),
            llm_index=ComponentStatus(status="DISABLED", error=None),
        ),
        current_user=CurrentUser(id=2, username="admin", is_superuser=True),
        ai_enabled=False,
    )


async def test_a_user_without_status_permission_gets_no_system_status(
    make_client: ClientFactory,
) -> None:
    overview = await reader(make_client, instance(status=STATUS_FORBIDDEN)).get_overview()

    assert overview.system_status is None
    assert overview.current_user is not None


async def test_a_user_without_ui_settings_permission_has_no_user_or_ai_flag(
    make_client: ClientFactory,
) -> None:
    overview = await reader(make_client, instance(ui_settings=UI_SETTINGS_FORBIDDEN)).get_overview()

    assert (overview.current_user, overview.ai_enabled) == (None, None)
    assert overview.system_status is not None


async def test_no_inbox_tag_means_the_inbox_count_is_unknown(make_client: ClientFactory) -> None:
    statistics = {**STATISTICS, "documents_inbox": None, "inbox_tag": None, "inbox_tags": None}
    responses = instance(statistics=httpx2.Response(200, json=statistics))

    assert (await reader(make_client, responses).get_overview()).statistics.inbox_documents is None


async def test_settings_without_the_ai_flag_mean_unknown(make_client: ClientFactory) -> None:
    settings = {key: value for key, value in SETTINGS.items() if key != "ai_enabled"}
    responses = instance(
        ui_settings=httpx2.Response(200, json={**UI_SETTINGS, "settings": settings})
    )

    assert (await reader(make_client, responses).get_overview()).ai_enabled is None


@pytest.mark.decision("decision-57")
@pytest.mark.parametrize("release", [{}, {"X-Version": ""}], ids=["missing", "empty"])
async def test_without_a_release_in_the_header_the_release_is_unknown(
    make_client: ClientFactory, release: dict[str, str]
) -> None:
    responses = instance()
    client = make_client(
        lambda request: httpx2.Response(
            responses[request.url.path].status_code,
            content=responses[request.url.path].content,
            headers={"X-Api-Version": "10", "Content-Type": "application/json", **release},
        )
    )

    overview = await PaperlessInstanceReader(client).get_overview()

    assert (overview.paperless_version, overview.api_version) == ("unknown", 10)


async def test_a_response_without_version_headers_is_unexpected(make_client: ClientFactory) -> None:
    client = make_client(lambda _: httpx2.Response(200, json=STATISTICS))

    with pytest.raises(PaperlessMcpError, match=r"unexpected response for GET /api/statistics/"):
        _ = await PaperlessInstanceReader(client).get_overview()


@pytest.mark.parametrize(
    ("responses", "error"),
    [
        pytest.param(
            instance(statistics=httpx2.Response(401, json={"detail": "Invalid token."})),
            AuthenticationFailed,
            id="statistics 401",
        ),
        pytest.param(
            instance(status=httpx2.Response(503, text="Service Unavailable")),
            UpstreamUnavailable,
            id="status 503",
        ),
        pytest.param(
            instance(ui_settings=httpx2.Response(500, text="Server Error")),
            UpstreamUnavailable,
            id="ui settings 500",
        ),
    ],
)
async def test_other_failures_are_not_hidden(
    make_client: ClientFactory, responses: dict[str, httpx2.Response], error: type[Exception]
) -> None:
    with pytest.raises(error):
        _ = await reader(make_client, responses).get_overview()


@pytest.mark.decision("decision-66")
async def test_a_status_error_holds_no_credentials_of_a_url(make_client: ClientFactory) -> None:
    said = "Error connecting to redis://user:pw@broker:6379"
    status = {**STATUS, "tasks": {**cast("Json", STATUS["tasks"]), "redis_error": said}}

    overview = await reader(
        make_client, instance(status=httpx2.Response(200, json=status))
    ).get_overview()

    assert overview.system_status is not None
    assert overview.system_status.redis.error == "Error connecting to redis://broker:6379"


async def test_every_component_keeps_its_own_error_message(make_client: ClientFactory) -> None:
    tasks = cast("Json", STATUS["tasks"])
    status = {
        **STATUS,
        "database": {**cast("Json", STATUS["database"]), "status": "ERROR", "error": "db down"},
        "tasks": {
            **tasks,
            "redis_error": "redis down",
            "celery_error": "celery down",
            "index_error": "index down",
            "classifier_error": "classifier down",
            "sanity_check_error": "sanity down",
            "llmindex_error": "llm down",
        },
    }

    overview = await reader(
        make_client, instance(status=httpx2.Response(200, json=status))
    ).get_overview()

    system = overview.system_status
    assert system is not None
    assert [
        component.error
        for component in (
            system.database,
            system.redis,
            system.celery,
            system.search_index,
            system.classifier,
            system.sanity_check,
            system.llm_index,
        )
    ] == [
        "db down",
        "redis down",
        "celery down",
        "index down",
        "classifier down",
        "sanity down",
        "llm down",
    ]


# --- the system report (PLMCP-89.3)

REPORT_STATUS: Json = {
    **STATUS,
    "database": {
        **cast("Json", STATUS["database"]),
        "migration_status": {
            "latest_migration": "documents.0099_x",
            "unapplied_migrations": ["documents.0100_y"],
        },
    },
    "tasks": {
        **cast("Json", STATUS["tasks"]),
        "classifier_last_trained": "2026-11-09T20:00:00Z",
        "sanity_check_last_run": "2026-11-09T19:00:00Z",
        "llmindex_last_modified": "2026-11-09T18:00:00Z",
    },
}


@pytest.mark.decision("decision-95")
async def test_the_report_has_everything_statistics_and_status_give(
    make_client: ClientFactory,
) -> None:
    responses = instance(status=httpx2.Response(200, json=REPORT_STATUS))

    report = await reader(make_client, responses).get_system_report()

    assert report == SystemReport(
        statistics=StatisticsReport(
            documents=2,
            inbox_documents=1,
            inbox_tags=(4,),
            file_types=(FileTypeCount(mime_type="application/pdf", documents=2),),
            characters=55,
            tags=4,
            correspondents=1,
            document_types=1,
            storage_paths=1,
            highest_serial_number=0,
        ),
        status=StatusReport(
            paperless_version="3.4.2",
            server_os="Linux-6.0-x86_64-with-glibc2.41",
            install_type="docker",
            storage_total_bytes=53217329152,
            storage_available_bytes=42602811392,
            database=DatabaseStatus(
                status="OK",
                error=None,
                type="sqlite",
                name="/usr/src/paperless/data/db.sqlite3",
                latest_migration="documents.0099_x",
                unapplied_migrations=("documents.0100_y",),
            ),
            redis=ServiceStatus(status="OK", error=None, address="redis://broker:6379"),
            celery=ServiceStatus(status="OK", error=None, address="celery@9464bd509d18"),
            search_index=TimedStatus(
                status="OK", error=None, last=datetime(2026, 11, 9, 21, 43, 38, 159239, tzinfo=UTC)
            ),
            classifier=TimedStatus(
                status="WARNING",
                error="No classifier training tasks found",
                last=datetime(2026, 11, 9, 20, tzinfo=UTC),
            ),
            sanity_check=TimedStatus(
                status="WARNING",
                error="No sanity check tasks found",
                last=datetime(2026, 11, 9, 19, tzinfo=UTC),
            ),
            llm_index=TimedStatus(
                status="DISABLED", error=None, last=datetime(2026, 11, 9, 18, tzinfo=UTC)
            ),
            tasks=TaskTotals(days=30, total=3, pending=0, success=3, failure=0),
        ),
    )


@pytest.mark.decision("decision-95")
async def test_the_report_of_a_user_without_the_status_permission_has_no_status(
    make_client: ClientFactory,
) -> None:
    statistics = {**STATISTICS, "documents_inbox": None, "inbox_tag": None, "inbox_tags": None}
    responses = instance(status=STATUS_FORBIDDEN, statistics=httpx2.Response(200, json=statistics))

    report = await reader(make_client, responses).get_system_report()

    assert report.status is None
    assert (report.statistics.inbox_documents, report.statistics.inbox_tags) == (None, ())


@pytest.mark.decision("decision-95")
@pytest.mark.parametrize("failing", ["statistics", "status"])
async def test_a_report_that_cannot_be_read_is_an_error(
    make_client: ClientFactory, failing: str
) -> None:
    responses = instance(**{failing: httpx2.Response(503, text="Service Unavailable")})

    with pytest.raises(UpstreamUnavailable):
        _ = await reader(make_client, responses).get_system_report()


def texts(value: object, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """The paths of every text, or null where one can be, of the status."""
    if isinstance(value, dict):
        found: list[tuple[str, ...]] = []
        for key, inner in cast("Json", value).items():
            found += texts(inner, (*path, key))
        return found
    named = path and ("error" in path[-1] or path[-1] == "unapplied_migrations")
    return [path] if named or isinstance(value, str) else []


TEXTS = [path for path in texts(REPORT_STATUS) if not path[-1].endswith(MOMENTS)]


@pytest.mark.decision("decision-95")
@pytest.mark.decision("decision-66")
@pytest.mark.parametrize("path", TEXTS, ids=".".join)
async def test_no_text_of_the_status_shows_the_credentials_of_a_url(
    path: tuple[str, ...],
) -> None:
    planted = "see redis://user:planted@broker:6379/0 now"
    status = cast("Json", json.loads(json.dumps(REPORT_STATUS)))
    inner = status
    for key in path[:-1]:
        inner = cast("Json", inner[key])
    inner[path[-1]] = [planted] if path[-1] == "unapplied_migrations" else planted
    client, _ = streaming(
        lambda request: httpx2.Response(
            200,
            json=STATISTICS if request.url.path == "/api/statistics/" else status,
            headers=VERSION_HEADERS,
        )
    )

    report = await PaperlessInstanceReader(client).get_system_report()

    assert "planted" not in report.model_dump_json()


def test_the_planted_texts_are_those_of_every_part() -> None:
    assert len(TEXTS) >= 20
    assert {path[0] for path in TEXTS} == {
        "pngx_version",
        "server_os",
        "install_type",
        "database",
        "tasks",
    }
