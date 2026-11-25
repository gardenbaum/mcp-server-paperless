"""Contract of TaskListReader and TaskListWriter: the same assertions on the fake and live one.

Tasks are started by hand only as `sanity_check`, which changes nothing; its row is acknowledged
afterwards. Other tests add tasks to the live stack meanwhile, so a test looks for its own rows.
"""

from typing import TYPE_CHECKING, cast

import pytest

from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.domain.task_lists import TaskFilters, TaskOrdering, TaskRow

if TYPE_CHECKING:
    from contract.conftest import Backend
    from mcp_server_paperless.domain.ports import TaskListReader
    from mcp_server_paperless.domain.task_lists import RunnableTask

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-8"),
    pytest.mark.decision("decision-93"),
]
LIST_TASKS = "TaskListReader.list_tasks"
COUNT, ACTIVE = "TaskListReader.count_tasks", "TaskListReader.list_active_tasks"
SUMMARY, VIEWER = "TaskListReader.summarize_tasks", "TaskListReader.get_viewer"
ACKNOWLEDGE_ALL = "TaskListWriter.acknowledge_all_tasks"
ACKNOWLEDGE, RUN = "TaskListWriter.acknowledge_tasks", "TaskListWriter.run_task"
UNKNOWN = 999_999
# What POST /api/tasks/run/ lists in its schema and refuses.
REFUSED = (
    *("apply_ai_suggestions", "build_share_link", "bulk_delete", "bulk_update"),
    *("check_workflows", "consume_file", "empty_trash", "index_optimize", "mail_fetch"),
    "reprocess_document",
)


@pytest.mark.contract(RUN)
@pytest.mark.contract(LIST_TASKS)
@pytest.mark.contract(ACKNOWLEDGE)
async def test_a_task_is_started_listed_and_acknowledged(backend: Backend) -> None:
    listing, writes = backend.admin.task_lists, backend.admin.task_list_writes

    task_id = await writes.run_task("sanity_check")

    found = None
    for page in range(1, 6):
        listed = await listing.list_tasks(
            TaskFilters(task_types=("sanity_check",), acknowledged=False), page=page, page_size=100
        )
        found = next((row for row in listed.rows if row.task_id == task_id), None)
        if found or listed.next_page is None:
            break
    assert found is not None
    assert (found.task_type, found.trigger_source, found.acknowledged) == (
        "sanity_check",
        "manual",
        False,
    )
    assert await writes.acknowledge_tasks([found.id]) == 1
    after = await listing.list_tasks(TaskFilters(acknowledged=True), page=1, page_size=100)
    assert found.id in {row.id for row in after.rows}
    with pytest.raises(Validation):
        _ = await writes.acknowledge_tasks([found.id, found.id])
    with pytest.raises(Validation):
        _ = await writes.acknowledge_tasks([UNKNOWN * 1000])


@pytest.mark.contract(LIST_TASKS)
async def test_tasks_are_paged_newest_first_and_filtered(backend: Backend) -> None:
    listing = backend.admin.task_lists
    _ = backend.consume(succeed=True)
    _ = backend.consume(succeed=False)

    first = await listing.list_tasks(TaskFilters(), page=1, page_size=2)
    failed = await listing.list_tasks(
        TaskFilters(states=("failure",), task_types=("consume_file",)), page=1, page_size=100
    )

    assert len(first.rows) == 2
    assert first.count >= 2
    assert [r.created for r in first.rows] == sorted((r.created for r in first.rows), reverse=True)
    assert failed.rows
    assert {r.state for r in failed.rows} == {"failure"}
    assert any(r.message for r in failed.rows)
    with pytest.raises(NotFound):
        _ = await listing.list_tasks(TaskFilters(), page=UNKNOWN, page_size=2)


@pytest.mark.contract(LIST_TASKS)
@pytest.mark.contract(ACKNOWLEDGE)
@pytest.mark.contract(RUN)
async def test_tasks_need_their_permissions_and_say_so(backend: Backend) -> None:
    nothing = backend.restricted(frozenset({"view_uisettings"}))
    viewer = backend.restricted(frozenset({"view_paperlesstask"}))

    for call in (
        nothing.task_lists.list_tasks(TaskFilters(), page=1, page_size=1),
        viewer.task_list_writes.acknowledge_tasks([1]),
        viewer.task_list_writes.run_task("sanity_check"),
    ):
        with pytest.raises(PermissionDenied) as raised:
            await call
        assert raised.value.hint


# --- filters and orderings


async def rows(listing: TaskListReader, filters: TaskFilters) -> dict[str, TaskRow]:
    """Every task the filters leave, by its Celery id."""
    found: dict[str, TaskRow] = {}
    page: int | None = 1
    while page is not None:
        listed = await listing.list_tasks(filters, page=page, page_size=100)
        found |= {row.task_id: row for row in listed.rows}
        page = listed.next_page
    return found


@pytest.mark.contract(LIST_TASKS)
async def test_every_filter_keeps_what_matches_and_leaves_out_the_rest(backend: Backend) -> None:
    listing = backend.admin.task_lists
    good, bad = backend.consume(succeed=True), backend.consume(succeed=False)
    check = await backend.admin.task_list_writes.run_task("sanity_check")
    everything = await rows(listing, TaskFilters())
    success, failure, manual = everything[good], everything[bad], everything[check]
    assert failure.message
    assert (success.owner, failure.owner, manual.owner) == (success.owner, success.owner, None)
    owner = cast("int", success.owner)

    cases: list[tuple[TaskFilters, set[str], set[str]]] = [
        (TaskFilters(states=("failure",)), {bad}, {good}),
        (TaskFilters(states=("failure", "success")), {good, bad}, set()),
        (TaskFilters(states=("revoked",)), set(), {good, bad}),
        (TaskFilters(task_types=("sanity_check",)), {check}, {good, bad}),
        (TaskFilters(task_types=("sanity_check", "consume_file")), {check, good, bad}, set()),
        (TaskFilters(trigger_sources=("api_upload",)), {good, bad}, {check}),
        (TaskFilters(trigger_sources=("api_upload", "manual")), {good, bad, check}, set()),
        (TaskFilters(acknowledged=False), {good, bad, check}, set()),
        (TaskFilters(acknowledged=True), set(), {good, bad, check}),
        (TaskFilters(is_complete=True), {good, bad}, set()),
        (TaskFilters(is_complete=False), set(), {good, bad}),
        (TaskFilters(owner=owner), {good, bad}, {check}),
        (TaskFilters(created_from=success.created), {good, bad, check}, set()),
        (TaskFilters(created_from=manual.created), {check}, {good, bad}),
        (TaskFilters(created_to=success.created), {good}, {bad, check}),
        (TaskFilters(name=cast("str", success.file_name)), {good}, {bad, check}),
        (TaskFilters(result=failure.message[:20]), {bad}, {good, check}),
        (TaskFilters(result=str(success.document_ids[0])), {good}, {bad, check}),
        (TaskFilters(states=("failure",), trigger_sources=("manual",)), set(), {good, bad, check}),
    ]
    for filters, kept, left_out in cases:
        found = await rows(listing, filters)
        assert kept <= found.keys(), filters
        assert left_out.isdisjoint(found), filters


@pytest.mark.contract(LIST_TASKS)
@pytest.mark.parametrize("name", ["Sanity Che", "manual"])
async def test_the_name_filter_also_finds_the_words_for_type_and_source(
    backend: Backend, name: str
) -> None:
    check = await backend.admin.task_list_writes.run_task("sanity_check")
    good = backend.consume(succeed=True)

    found = await rows(backend.admin.task_lists, TaskFilters(name=name))

    assert check in found
    assert good not in found


@pytest.mark.contract(LIST_TASKS)
async def test_an_owner_who_does_not_exist_is_refused(backend: Backend) -> None:
    with pytest.raises(Validation, match="owner"):
        _ = await backend.admin.task_lists.list_tasks(
            TaskFilters(owner=UNKNOWN), page=1, page_size=1
        )


@pytest.mark.contract(LIST_TASKS)
async def test_every_ordering_sorts_and_its_reverse_turns_it_round(backend: Backend) -> None:
    _ = backend.consume(succeed=True), backend.consume(succeed=False)
    _ = await backend.admin.task_list_writes.run_task("sanity_check")
    for field in (
        *("created", "finished", "state", "task_type"),
        *("duration_seconds", "wait_time_seconds"),
    ):
        for sign in ("", "-"):
            ordering = cast("TaskOrdering", f"{sign}{field}")
            listed = await backend.admin.task_lists.list_tasks(
                TaskFilters(), ordering=ordering, page=1, page_size=100
            )
            # Where the tasks without the value come is the database's choice.
            shown = [cast("object", row.model_dump()[field]) for row in listed.rows]
            values = cast("list[float]", [v for v in shown if v is not None])
            assert values == sorted(values, reverse=sign == "-"), ordering
            assert values, ordering


@pytest.mark.contract(LIST_TASKS)
async def test_the_row_of_an_upload_has_its_file_name_and_owner(backend: Backend) -> None:
    good = backend.consume(succeed=True)

    row = (await rows(backend.admin.task_lists, TaskFilters(states=("success",))))[good]

    assert row.file_name
    assert row.owner is not None
    assert row.wait_time_seconds is None or row.wait_time_seconds >= 0


@pytest.mark.contract(LIST_TASKS)
async def test_a_user_sees_own_and_unowned_tasks_only(backend: Backend) -> None:
    good = backend.consume(succeed=True)
    check = await backend.admin.task_list_writes.run_task("sanity_check")
    viewer = backend.restricted(frozenset({"view_paperlesstask"}))

    found = await rows(viewer.task_lists, TaskFilters())

    assert check in found
    assert good not in found


@pytest.mark.contract(RUN)
@pytest.mark.parametrize("kind", REFUSED)
async def test_paperless_starts_no_other_type_by_hand(backend: Backend, kind: str) -> None:
    with pytest.raises(Validation, match="cannot be manually triggered"):
        _ = await backend.admin.task_list_writes.run_task(cast("RunnableTask", kind))


# --- counts, running tasks and the summary (PLMCP-89.2)


@pytest.mark.contract(COUNT)
async def test_the_counts_take_the_filters_and_add_up(backend: Backend) -> None:
    listing = backend.admin.task_lists
    good, bad = backend.consume(succeed=True), backend.consume(succeed=False)
    everything = await rows(listing, TaskFilters())
    owner, name = everything[good].owner, everything[bad].file_name

    total = await listing.count_tasks(TaskFilters())
    uploads = await listing.count_tasks(
        TaskFilters(task_types=("consume_file",), trigger_sources=("api_upload",), owner=owner)
    )
    [one, other, none] = [
        await listing.count_tasks(filters)
        for filters in (
            TaskFilters(name=name, acknowledged=False),
            TaskFilters(name=name, created_from=everything[bad].created),
            TaskFilters(name=name, created_to=everything[good].created),
        )
    ]

    assert total.total == total.needs_attention + total.in_progress + total.completed
    assert uploads.total >= 2
    assert uploads.total <= total.total
    assert (one.total, one.needs_attention, one.completed) == (1, 1, 0)
    assert other == one
    assert none.total == 0
    by_error = await listing.count_tasks(TaskFilters(name=name, result="no such words here"))
    assert by_error.total == 0


@pytest.mark.contract(ACTIVE)
async def test_the_running_tasks_are_not_ended(backend: Backend) -> None:
    good = backend.consume(succeed=True)
    _ = await backend.admin.task_list_writes.run_task("sanity_check")

    active = await backend.admin.task_lists.list_active_tasks()

    assert len(active) <= 50
    assert {row.state for row in active} <= {"pending", "started"}
    assert good not in {row.task_id for row in active}


@pytest.mark.contract(SUMMARY)
async def test_the_summary_has_the_types_of_the_last_days(backend: Backend) -> None:
    _ = backend.consume(succeed=True), backend.consume(succeed=False)

    found = {s.task_type: s for s in await backend.admin.task_lists.summarize_tasks(days=1)}

    uploads = found["consume_file"]
    assert uploads.total >= 2
    assert uploads.success >= 1
    assert uploads.failure >= 1
    assert uploads.last_run is not None
    assert uploads.last_success is not None
    assert uploads.last_failure is not None


@pytest.mark.contract(VIEWER)
@pytest.mark.contract(SUMMARY)
@pytest.mark.contract(COUNT)
async def test_the_summary_is_wider_than_the_list_for_who_may_view_the_status(
    backend: Backend,
) -> None:
    _ = backend.consume(succeed=True)
    plain = backend.restricted(frozenset({"view_paperlesstask", "view_uisettings"}))
    monitor = backend.restricted(frozenset({"view_system_monitoring", "view_uisettings"}))

    async def uploads(listing: TaskListReader) -> int:
        found = {s.task_type: s.total for s in await listing.summarize_tasks(days=1)}
        return found.get("consume_file", 0)

    viewers = [await ports.task_lists.get_viewer() for ports in (backend.admin, plain, monitor)]
    assert [(v.sees_all, v.summary_of_all) for v in viewers] == [
        (True, True),
        (False, False),
        (False, True),
    ]
    assert len({v.id for v in viewers}) == 3
    assert await uploads(backend.admin.task_lists) >= 1
    assert await uploads(monitor.task_lists) == await uploads(backend.admin.task_lists)
    assert await uploads(plain.task_lists) < await uploads(backend.admin.task_lists)
    with pytest.raises(PermissionDenied):
        _ = await monitor.task_lists.count_tasks(TaskFilters())


@pytest.mark.contract(COUNT)
@pytest.mark.contract(ACTIVE)
@pytest.mark.contract(SUMMARY)
@pytest.mark.contract(VIEWER)
@pytest.mark.contract(ACKNOWLEDGE_ALL)
async def test_the_aggregates_need_their_permissions(backend: Backend) -> None:
    nothing = backend.restricted(frozenset({"view_document"}))
    viewer = backend.restricted(frozenset({"view_paperlesstask"}))

    for call in (
        nothing.task_lists.count_tasks(TaskFilters()),
        nothing.task_lists.list_active_tasks(),
        nothing.task_lists.summarize_tasks(days=1),
        nothing.task_lists.get_viewer(),
        viewer.task_list_writes.acknowledge_all_tasks(),
    ):
        with pytest.raises(PermissionDenied):
            _ = await call


@pytest.mark.contract(ACKNOWLEDGE_ALL)
async def test_a_user_acknowledges_own_and_unowned_tasks_only_and_staff_every_one(
    backend: Backend,
) -> None:
    good = backend.consume(succeed=True)
    check = await backend.admin.task_list_writes.run_task("sanity_check")
    user = backend.restricted(frozenset({"view_paperlesstask", "change_paperlesstask"}))
    new = TaskFilters(acknowledged=False)

    assert await user.task_list_writes.acknowledge_all_tasks() >= 1

    left = await rows(backend.admin.task_lists, new)
    assert good in left
    assert check not in left
    assert await backend.admin.task_list_writes.acknowledge_all_tasks() >= 1
    seen = await rows(backend.admin.task_lists, TaskFilters(acknowledged=True))
    assert {good, check} <= seen.keys()
