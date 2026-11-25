"""PaperlessTaskListReader and PaperlessTaskListWriter on the Paperless 3.5.1 wire format."""

from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from adapter.test_admin import Json, Recording, forbidden, not_found, ok, page
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.domain.task_lists import TaskFilters, TaskOrdering, TaskRow
from mcp_server_paperless.paperless.task_lists import (
    PaperlessTaskListReader,
    PaperlessTaskListWriter,
)

if TYPE_CHECKING:
    import httpx2

    from adapter.conftest import ClientFactory

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-93")]

# The query string of every request as pairs: some names are repeated.
type Pairs = list[tuple[str, str]]


class Queries:
    """Answers every request with an empty page and records its query."""

    def __init__(self) -> None:
        self.sent: list[Pairs] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(list(request.url.params.multi_items()))
        return page()


TASK_WIRE: Json = {
    "id": 41,
    "task_id": "8c6f5f2e-6e4d-4a1a-9d45-0a8c0b2f3c11",
    "task_type": "consume_file",
    "task_type_display": "Consume File",
    "trigger_source": "api_upload",
    "trigger_source_display": "API Upload",
    "status": "failure",
    "status_display": "Failure",
    "date_created": "2026-11-17T08:00:00Z",
    "date_started": "2026-11-17T08:00:01Z",
    "date_done": "2026-11-17T08:00:05.5Z",
    "duration_seconds": 4.5,
    "wait_time_seconds": 1.0,
    "input_data": {"filename": "scan.pdf"},
    "result_data": {"error_message": "scan.pdf: not a PDF\nTraceback: secret path"},
    "related_document_ids": [],
    "acknowledged": False,
    "owner": 2,
}


TASK = TaskRow(
    id=41,
    task_id="8c6f5f2e-6e4d-4a1a-9d45-0a8c0b2f3c11",
    task_type="consume_file",
    trigger_source="api_upload",
    state="failure",
    created=datetime(2026, 11, 17, 8, tzinfo=UTC),
    started=datetime(2026, 11, 17, 8, 0, 1, tzinfo=UTC),
    finished=datetime(2026, 11, 17, 8, 0, 5, 500000, tzinfo=UTC),
    duration_seconds=4.5,
    document_ids=(),
    acknowledged=False,
    message="scan.pdf: not a PDF",
    file_name="scan.pdf",
    owner=2,
    wait_time_seconds=1.0,
)


async def test_tasks_are_listed_newest_first_with_their_database_id(
    make_client: ClientFactory,
) -> None:
    link = "http://paperless.test/api/tasks/?page=3"
    server = Recording(page(TASK_WIRE, count=40, next_link=link))

    found = await PaperlessTaskListReader(make_client(server)).list_tasks(
        TaskFilters(), page=2, page_size=10
    )

    assert (found.count, found.next_page, found.rows) == (40, 3, (TASK,))
    assert server.sent == [
        ("GET", "/api/tasks/", None, {"page": "2", "page_size": "10", "ordering": "-date_created"})
    ]


async def test_tasks_are_filtered_by_state_type_and_acknowledgement(
    make_client: ClientFactory,
) -> None:
    server = Recording(page())

    _ = await PaperlessTaskListReader(make_client(server)).list_tasks(
        TaskFilters(states=("failure",), task_types=("sanity_check",), acknowledged=False),
        page=1,
        page_size=25,
    )

    assert server.sent[0][3] == {
        "page": "1",
        "page_size": "25",
        "ordering": "-date_created",
        "status": "failure",
        "task_type": "sanity_check",
        "acknowledged": "false",
    }


async def test_a_task_without_an_error_message_has_none(make_client: ClientFactory) -> None:
    wire = {**TASK_WIRE, "status": "success", "result_data": {"document_id": 7}}
    wire = {**wire, "related_document_ids": [7], "date_done": None, "duration_seconds": None}
    server = Recording(page(wire))

    found = await PaperlessTaskListReader(make_client(server)).list_tasks(
        TaskFilters(), page=1, page_size=25
    )

    [row] = found.rows
    assert (row.message, row.document_ids, row.finished, row.duration_seconds) == (
        None,
        (7,),
        None,
        None,
    )


async def test_a_task_page_past_the_end_is_not_found(make_client: ClientFactory) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await PaperlessTaskListReader(make_client(Recording(not_found()))).list_tasks(
            TaskFilters(), page=9, page_size=25
        )

    assert raised.value.describe() == (
        "Page 9 of the tasks does not exist. "
        "Hint: List the tasks with page=1; count and next_page tell how far it goes."
    )


async def test_listing_tasks_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessTaskListReader(make_client(Recording(forbidden()))).list_tasks(
            TaskFilters(), page=1, page_size=25
        )

    assert (
        raised.value.hint == "It needs the view_paperlesstask permission, or must be a superuser."
    )


async def test_tasks_are_acknowledged_by_id(make_client: ClientFactory) -> None:
    server = Recording(ok({"result": 2}))

    count = await PaperlessTaskListWriter(make_client(server)).acknowledge_tasks([41, 42])

    assert count == 2
    assert server.sent == [("POST", "/api/tasks/acknowledge/", {"tasks": [41, 42]}, {})]


async def test_acknowledging_unknown_tasks_is_a_validation_error(
    make_client: ClientFactory,
) -> None:
    server = Recording(
        ok({"tasks": ["Some tasks in tasks don't exist or were specified twice."]}, 400)
    )

    with pytest.raises(Validation) as raised:
        _ = await PaperlessTaskListWriter(make_client(server)).acknowledge_tasks([999])

    assert "don't exist or were specified twice" in raised.value.message


async def test_acknowledging_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessTaskListWriter(make_client(Recording(forbidden()))).acknowledge_tasks(
            [1]
        )

    assert raised.value.hint == (
        "It needs the change_paperlesstask permission, or must be a superuser."
    )


async def test_a_task_is_started_and_its_id_returned_without_a_retry(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok({"task_id": "abc-123"}))

    started = await PaperlessTaskListWriter(make_client(server)).run_task("sanity_check")

    assert started == "abc-123"
    assert server.sent == [("POST", "/api/tasks/run/", {"task_type": "sanity_check"}, {})]


async def test_starting_a_task_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessTaskListWriter(make_client(Recording(forbidden()))).run_task("llm_index")

    assert raised.value.describe() == (
        "The Paperless user may not start tasks. Hint: Only a superuser may start tasks."
    )


async def test_a_task_paperless_refuses_to_start_is_a_validation_error(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok({"error": "Task type 'x' cannot be manually triggered"}, 400))

    with pytest.raises(Validation):
        _ = await PaperlessTaskListWriter(make_client(server)).run_task("sanity_check")


@pytest.mark.parametrize(
    "result", [None, {"error_message": None}, {"error_message": " \n "}, "text"]
)
async def test_a_task_whose_result_has_no_error_text_has_no_message(
    make_client: ClientFactory, result: object
) -> None:
    server = Recording(page({**TASK_WIRE, "result_data": result}))

    found = await PaperlessTaskListReader(make_client(server)).list_tasks(
        TaskFilters(), page=1, page_size=25
    )

    assert found.rows[0].message is None


@pytest.mark.decision("decision-66")
async def test_a_listed_task_holds_no_credentials_of_a_url_even_where_it_is_cut(
    make_client: ClientFactory,
) -> None:
    said = {"error_message": "to https://user:p\nw@h.example"}
    server = Recording(page({**TASK_WIRE, "result_data": said}))

    found = await PaperlessTaskListReader(make_client(server)).list_tasks(
        TaskFilters(), page=1, page_size=25
    )

    assert found.rows[0].message == "to a URL that is not shown"


async def test_the_acknowledged_filter_is_sent_as_true_or_false(
    make_client: ClientFactory,
) -> None:
    server = Recording(page(), page())
    reader = PaperlessTaskListReader(make_client(server))

    for flag in (True, False):
        _ = await reader.list_tasks(TaskFilters(acknowledged=flag), page=1, page_size=5)

    assert [sent[3]["acknowledged"] for sent in server.sent] == ["true", "false"]


# --- filters and orderings

CET = timezone(timedelta(hours=1))


@pytest.mark.parametrize(
    ("filters", "sent"),
    [
        (
            TaskFilters(states=("failure", "revoked")),
            [("status", "failure"), ("status", "revoked")],
        ),
        (
            TaskFilters(task_types=("mail_fetch", "llm_index")),
            [("task_type", "mail_fetch"), ("task_type", "llm_index")],
        ),
        (
            TaskFilters(trigger_sources=("web_ui", "email_consume")),
            [("trigger_source", "web_ui"), ("trigger_source", "email_consume")],
        ),
        (TaskFilters(acknowledged=True), [("acknowledged", "true")]),
        (TaskFilters(is_complete=True), [("is_complete", "true")]),
        (TaskFilters(is_complete=False), [("is_complete", "false")]),
        (TaskFilters(owner=7), [("owner", "7")]),
        (TaskFilters(owner=0), [("owner", "0")]),
        (TaskFilters(name="a b&c+d"), [("name", "a b&c+d")]),
        (TaskFilters(result="50%"), [("result", "50%")]),
        (
            TaskFilters(created_from=datetime(2026, 11, 1, 8, tzinfo=CET)),
            [("date_created_after", "2026-11-01T08:00:00+01:00")],
        ),
        (
            TaskFilters(created_to=datetime(2026, 11, 2, tzinfo=UTC)),
            [("date_created_before", "2026-11-02T00:00:00+00:00")],
        ),
    ],
)
async def test_every_filter_is_sent_under_the_name_paperless_has(
    make_client: ClientFactory, filters: TaskFilters, sent: Pairs
) -> None:
    server = Queries()

    _ = await PaperlessTaskListReader(make_client(server)).list_tasks(filters, page=1, page_size=5)

    [query] = server.sent
    plain = [("page", "1"), ("page_size", "5"), ("ordering", "-date_created")]
    assert sorted(query) == sorted([*plain, *sent])


@pytest.mark.parametrize(
    ("ordering", "sent"),
    [
        ("-created", "-date_created"),
        ("created", "date_created"),
        ("finished", "date_done,-date_created"),
        ("-finished", "-date_done,-date_created"),
        ("state", "status,-date_created"),
        ("-state", "-status,-date_created"),
        ("task_type", "task_type,-date_created"),
        ("-task_type", "-task_type,-date_created"),
        ("duration_seconds", "duration_seconds,-date_created"),
        ("-duration_seconds", "-duration_seconds,-date_created"),
        ("wait_time_seconds", "wait_time_seconds,-date_created"),
        ("-wait_time_seconds", "-wait_time_seconds,-date_created"),
    ],
)
async def test_every_ordering_has_the_newest_first_where_values_are_equal(
    make_client: ClientFactory, ordering: TaskOrdering, sent: str
) -> None:
    server = Queries()

    _ = await PaperlessTaskListReader(make_client(server)).list_tasks(
        TaskFilters(), ordering=ordering, page=1, page_size=5
    )

    assert dict(server.sent[0])["ordering"] == sent


async def test_of_the_input_only_the_file_name_is_read(make_client: ClientFactory) -> None:
    given = {
        "filename": "scan.pdf",
        "source_path": "/srv/consume/scan.pdf",
        "overrides": {"title": "Private title", "owner_id": 2},
    }
    server = Recording(page({**TASK_WIRE, "input_data": given}))

    found = await PaperlessTaskListReader(make_client(server)).list_tasks(
        TaskFilters(), page=1, page_size=5
    )

    [row] = found.rows
    assert row.file_name == "scan.pdf"
    assert "/srv" not in found.model_dump_json()
    assert "Private" not in found.model_dump_json()


@pytest.mark.parametrize("given", [{}, {"filename": None}, {"filename": ""}, None, [], "text"])
async def test_a_task_without_a_file_has_no_file_name(
    make_client: ClientFactory, given: object
) -> None:
    wire = {**TASK_WIRE, "input_data": given, "owner": None, "wait_time_seconds": None}
    server = Recording(page(wire))

    found = await PaperlessTaskListReader(make_client(server)).list_tasks(
        TaskFilters(), page=1, page_size=5
    )

    [row] = found.rows
    assert (row.file_name, row.owner, row.wait_time_seconds) == (None, None, None)
