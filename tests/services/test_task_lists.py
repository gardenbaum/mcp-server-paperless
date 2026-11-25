"""TaskListService: listing, acknowledging and starting background tasks.

Fakes of "nothing may be written" cases are created without `allow_writes`. Seeded users: admin 2
(superuser), reader 3.
"""

from datetime import UTC, datetime, timedelta
from typing import final

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, FakeTask, FakeUser, seeded

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.task_lists import TaskListService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-15"),
    pytest.mark.decision("decision-26"),
    pytest.mark.decision("decision-93"),
]


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True) -> None:
        self.state: FakeState = seeded()
        self.fake: FakePaperless = FakePaperless(self.state, user=user, allow_writes=writes)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.service: TaskListService = TaskListService(self.fake, self.fake, taxonomy)


async def refusal(call: object) -> str:
    with pytest.raises(ConfirmationRequired) as raised:
        await call  # pyright: ignore[reportGeneralTypeIssues] -- test data built from dicts
    assert raised.value.hint == CONFIRM_HINT
    assert raised.value.message.startswith("Nothing was changed. Confirming would ")
    return raised.value.message


async def failure(call: object, kind: type[Validation]) -> str:
    with pytest.raises(kind) as raised:
        await call  # pyright: ignore[reportGeneralTypeIssues] -- test data built from dicts
    return raised.value.describe()


def task(number: int, **fields: object) -> FakeTask:
    return FakeTask(
        id=number,
        task_id=f"uuid-{number}",
        created=datetime(2026, 11, 17, 8, number, tzinfo=UTC),
        **fields,  # pyright: ignore[reportArgumentType] -- test data built from dicts
    )


async def test_tasks_are_listed_newest_first_with_filters_and_paging() -> None:
    setup = Setup()
    setup.state.tasks = [
        task(1, status="success"),
        task(2, status="failure", message="boom"),
        task(3, status="failure", task_type="sanity_check"),
    ]

    everything = await setup.service.list_tasks(page=1, page_size=2)
    failed = await setup.service.list_tasks(state="failure", task_type="consume_file")

    assert (everything.count, everything.page, everything.next_page) == (3, 1, 2)
    assert [t.id for t in everything.tasks] == [3, 2]
    assert [t.id for t in failed.tasks] == [2]
    assert failed.tasks[0].message == "boom"
    open_ones = await setup.service.list_tasks(acknowledged=False)
    assert open_ones.count == 3


async def test_a_page_past_the_end_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().service.list_tasks(page=9)


async def test_tasks_are_acknowledged_without_confirm() -> None:
    setup = Setup()
    setup.state.tasks = [task(1), task(2)]

    outcome = await setup.service.acknowledge_tasks([1, 2])

    assert outcome.acknowledged == 2
    assert "2 tasks" in outcome.message
    assert all(t.acknowledged for t in setup.state.tasks)


async def test_acknowledging_one_task_and_unknown_or_repeated_ids() -> None:
    setup = Setup()
    setup.state.tasks = [task(1)]

    assert "1 task " in (await setup.service.acknowledge_tasks([1])).message
    with pytest.raises(Validation):
        _ = await setup.service.acknowledge_tasks([1, 1])
    with pytest.raises(Validation):
        _ = await setup.service.acknowledge_tasks([99])


async def test_a_sanity_check_runs_without_confirm() -> None:
    setup = Setup()

    started = await setup.service.run_task("sanity_check")

    [created] = setup.state.tasks
    assert (started.task_id, started.task_type) == (created.task_id, "sanity_check")
    assert created.trigger_source == "manual"
    assert "get_task" in started.message


@pytest.mark.parametrize(
    ("kind", "words"),
    [
        ("train_classifier", ["train the classifier again", "automatic matching"]),
        ("llm_index", ["rebuild the AI index", "sends the text of every document"]),
    ],
)
async def test_the_other_tasks_need_confirm_and_say_what_they_do(
    kind: str, words: list[str]
) -> None:
    setup = Setup(writes=False)

    message = await refusal(setup.service.run_task(kind))  # pyright: ignore[reportArgumentType] -- test data built from dicts

    for word in words:
        assert word in message
    assert setup.state.tasks == []
    confirmed = await Setup().service.run_task(kind, confirm=True)  # pyright: ignore[reportArgumentType] -- test data built from dicts
    assert confirmed.task_type == kind


async def test_only_a_superuser_starts_tasks() -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await Setup(READER[0]).service.run_task("sanity_check")

    assert raised.value.hint


async def test_the_default_page_has_twenty_five_tasks_and_the_filter_is_passed_on() -> None:
    setup = Setup()
    setup.state.tasks = [
        FakeTask(id=n, task_id=f"u{n}", acknowledged=n % 2 == 0) for n in range(1, 41)
    ]

    first = await setup.service.list_tasks()
    seen = await setup.service.list_tasks(acknowledged=True, page_size=100)

    assert len(first.tasks) == 25
    assert seen.count == 20
    assert all(t.acknowledged for t in seen.tasks)


async def test_acknowledging_a_repeated_id_is_refused_with_a_hint() -> None:
    assert await failure(Setup().service.acknowledge_tasks([1, 1]), Validation) == (
        "A task id is listed twice. Hint: List every id once."
    )


# --- filters and orderings


AT = datetime(2026, 11, 17, 8, tzinfo=UTC)


def several() -> Setup:
    setup = Setup()
    setup.state.tasks = [
        task(1, status="success", file_name="one.pdf", owner=2, document_ids=(7,)),
        task(2, status="failure", message="boom", file_name="two.pdf", owner=3),
        task(3, status="started", task_type="sanity_check", trigger_source="manual"),
        task(4, status="revoked", task_type="mail_fetch", trigger_source="scheduled"),
    ]
    return setup


@pytest.mark.parametrize(
    ("given", "ids"),
    [
        ({}, [4, 3, 2, 1]),
        ({"state": "failure"}, [2]),
        ({"state": ["failure", "revoked"]}, [4, 2]),
        ({"task_type": "mail_fetch"}, [4]),
        ({"task_type": ["mail_fetch", "sanity_check"]}, [4, 3]),
        ({"trigger_source": "manual"}, [3]),
        ({"trigger_source": ["manual", "api_upload"]}, [3, 2, 1]),
        ({"is_complete": True}, [4, 2, 1]),
        ({"is_complete": False}, [3]),
        ({"owner": 3}, [2]),
        ({"owner": "3"}, [2]),
        ({"owner": "e2e-reader"}, [2]),
        ({"name": "TWO.p"}, [2]),
        ({"name": "Mail Fet"}, [4]),
        ({"result": "BOO"}, [2]),
        ({"result": "7"}, [1]),
        ({"created_from": AT + timedelta(minutes=3)}, [4, 3]),
        ({"created_to": AT + timedelta(minutes=2)}, [2, 1]),
        ({"state": ["failure", "success"], "owner": 2}, [1]),
        ({"ordering": "created"}, [1, 2, 3, 4]),
        ({"ordering": "state"}, [2, 4, 3, 1]),
        ({"ordering": "-task_type"}, [3, 4, 2, 1]),
    ],
)
async def test_every_filter_and_ordering_reaches_paperless(
    given: dict[str, object], ids: list[int]
) -> None:
    found = await several().service.list_tasks(**given)  # pyright: ignore[reportArgumentType] -- test data built from dicts

    assert [row.id for row in found.tasks] == ids
    assert found.count == len(ids)


async def test_a_row_says_its_file_owner_and_waiting_time() -> None:
    setup = Setup()
    setup.state.tasks = [task(1, file_name="a.pdf", owner=2, wait_time_seconds=1.5), task(2)]

    bare, full = (await setup.service.list_tasks()).tasks

    assert (full.file_name, full.owner, full.wait_time_seconds) == ("a.pdf", 2, 1.5)
    assert (bare.file_name, bare.owner, bare.wait_time_seconds) == (None, None, None)


@pytest.mark.parametrize("field", ["name", "result"])
@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
async def test_a_blank_text_is_refused_as_a_filter(field: str, blank: str) -> None:
    """Paperless would ignore it and answer every task."""
    text = await failure(several().service.list_tasks(**{field: blank}), Validation)  # pyright: ignore[reportArgumentType] -- test data built from dicts

    assert text == f"{field} must not be blank. Hint: Give some text, or leave {field} out."


async def test_an_empty_list_is_refused_as_a_filter() -> None:
    """Sent on, it would be no filter at all."""
    service = several().service
    for field, call in (
        ("state", service.list_tasks(state=[])),
        ("task_type", service.list_tasks(task_type=[])),
        ("trigger_source", service.list_tasks(trigger_source=[])),
    ):
        text = await failure(call, Validation)
        assert text == f"{field} must name at least one value. Hint: Leave {field} out for all."


async def test_an_unknown_owner_is_refused() -> None:
    with pytest.raises(NotFound):
        _ = await several().service.list_tasks(owner="nobody")


async def test_a_user_without_the_right_to_list_users_gives_the_id_of_an_owner() -> None:
    setup = several()
    permissions = frozenset({"view_paperlesstask"})
    setup.state.users.append(FakeUser(id=9, username="viewer", permissions=permissions))
    setup.state.tasks.append(task(5, owner=9))
    fake = FakePaperless(setup.state, user="viewer")
    taxonomy = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=3600.0)
    service = TaskListService(fake, fake, taxonomy)

    assert [row.id for row in (await service.list_tasks(owner=9)).tasks] == [5]
    with pytest.raises(PermissionDenied) as raised:
        _ = await service.list_tasks(owner="viewer")
    assert raised.value.hint == "Pass the owner's user id instead of the username."
