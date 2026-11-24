"""Webhook URLs without their credentials, and what writing them back does."""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.errors import ConfirmationRequired, Validation
from mcp_server_paperless.domain.urls import URL_NOT_SHOWN
from mcp_server_paperless.domain.workflows import (
    ActionInput,
    WebhookSettings,
    Workflow,
    WorkflowAction,
    WorkflowCreate,
    WorkflowEdit,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.workflows import WorkflowService

if TYPE_CHECKING:
    from pydantic import JsonValue

    from mcp_server_paperless.domain.workflows import ActionInfo, WorkflowInfo

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-66")]

SECRET = "https://user:pw@h.example/x?k=1"
SHOWN = "https://h.example/x?k=1"
OTHER = "https://other:pw2@h.example/x?k=1"
UNCLEAR = "https://us/er:pw@h.example/x"


def hooked(number: int, url: str) -> WorkflowAction:
    return WorkflowAction(id=number, type=4, settings={"webhook": {"url": url}})


def given(url: str, number: int | None = None) -> ActionInput:
    return ActionInput(type="webhook", id=number, webhook=WebhookSettings(url=url))


def seeding(*actions: WorkflowAction, enabled: bool = False) -> tuple[WorkflowService, FakeState]:
    state = seeded()
    state.workflows.append(
        Workflow(id=1, name="w", order=0, enabled=enabled, triggers=(), actions=actions)
    )
    fake = FakePaperless(state, user=ADMIN[0], allow_writes=True)
    resolver = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=3600.0)
    return WorkflowService(fake, fake, resolver), state


def url(action: WorkflowAction | ActionInfo) -> JsonValue:
    return cast("dict[str, dict[str, JsonValue]]", action.settings)["webhook"]["url"]


async def write(
    service: WorkflowService, *actions: ActionInput, confirm: bool = False
) -> WorkflowInfo:
    return await service.update("w", WorkflowEdit(actions=actions), confirm=confirm)


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        (SECRET, SHOWN),
        ("https://user@h.example/x", "https://h.example/x"),
        ("https://@h.example/x", "https://h.example/x"),
        (UNCLEAR, URL_NOT_SHOWN),
        ("https://a@b:pw@h.example/x", URL_NOT_SHOWN),
    ],
)
async def test_results_leave_the_credentials_out_and_say_so(stored: str, shown: str) -> None:
    service, _ = seeding(hooked(7, stored))

    read = await service.get("w")
    listed = (await service.list()).workflows[0]
    renamed = await service.update("w", WorkflowEdit(name="n"), confirm=False)

    for info in (read, listed, renamed):
        assert url(info.actions[0]) == shown
        assert info.actions[0].shown_in_part == ("webhook.url",)


async def test_a_url_without_credentials_is_shown_whole() -> None:
    service, _ = seeding(hooked(7, SHOWN), WorkflowAction(id=8, type=6))

    info = await service.get("w")

    assert url(info.actions[0]) == SHOWN
    assert [a.shown_in_part for a in info.actions] == [(), ()]


async def test_a_created_workflow_answers_without_credentials() -> None:
    service, state = seeding()

    made = await service.create(
        WorkflowCreate(name="n", actions=(given(SECRET),), enabled=False), confirm=False
    )

    assert url(state.workflows[-1].actions[0]) == SECRET
    assert url(made.actions[0]) == SHOWN
    assert made.actions[0].shown_in_part == ("webhook.url",)
    assert made.note is None


@pytest.mark.parametrize("stored", [SECRET, UNCLEAR])
async def test_a_workflow_written_back_keeps_the_stored_credentials(stored: str) -> None:
    service, state = seeding(hooked(7, stored), hooked(8, SHOWN))
    first, second = (await service.get("w")).actions

    done = await write(
        service, given(str(url(second)), second.id), given(str(url(first)), first.id)
    )

    assert [url(a) for a in state.workflows[0].actions] == [SHOWN, stored]
    assert done.note == "Kept as stored, with any credentials: the webhook URL of action 2."


async def test_two_actions_with_one_shown_url_keep_their_own_credentials() -> None:
    service, state = seeding(hooked(7, SECRET), hooked(8, OTHER))

    done = await write(service, given(SHOWN, 8), given(SHOWN, 7))

    assert [url(a) for a in state.workflows[0].actions] == [OTHER, SECRET]
    assert done.note == "Kept as stored, with any credentials: the webhook URL of actions 1, 2."


async def test_a_stored_url_written_whole_is_written_without_a_word() -> None:
    service, state = seeding(hooked(7, SECRET))

    done = await write(service, given(SECRET, 7))

    assert url(state.workflows[0].actions[0]) == SECRET
    assert done.note is None


@pytest.mark.parametrize("number", [None, 99, 8, 9])
@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize(("stored", "shown"), [(SECRET, SHOWN), (UNCLEAR, URL_NOT_SHOWN)])
async def test_a_shown_url_is_refused_where_its_id_does_not_tell_the_stored_one(
    *, number: int | None, confirm: bool, stored: str, shown: str
) -> None:
    service, state = seeding(
        hooked(7, stored), hooked(8, "https://else:pw@h.example/y"), WorkflowAction(id=9, type=6)
    )
    before = state.workflows[0]

    with pytest.raises(Validation) as raised:
        _ = await write(
            service, given("https://h.example/z"), given(shown, number), confirm=confirm
        )

    assert "The webhook URL of action 2" in raised.value.message
    assert "https://@host/path" in str(raised.value.hint)
    assert "pw" not in f"{raised.value.message}{raised.value.hint}"
    assert state.workflows[0] == before


async def test_one_id_does_not_give_its_credentials_to_two_actions() -> None:
    service, state = seeding(hooked(7, SECRET))
    before = state.workflows[0]

    with pytest.raises(Validation, match="actions 1, 2 have the id 7"):
        _ = await write(service, given(SHOWN, 7), given(SHOWN, 7))

    assert state.workflows[0] == before


async def test_the_placeholder_is_never_written() -> None:
    service, state = seeding()

    with pytest.raises(Validation, match="The webhook URL of action 1"):
        _ = await service.create(
            WorkflowCreate(name="n", actions=(given(URL_NOT_SHOWN),), enabled=False), confirm=True
        )

    assert len(state.workflows) == 1


@pytest.mark.parametrize("empty", ["@", ":@"])
@pytest.mark.parametrize("number", [7, None])
async def test_an_empty_user_name_removes_the_credentials(empty: str, number: int | None) -> None:
    service, state = seeding(hooked(7, SECRET))

    done = await write(service, given(f"https://{empty}h.example/x?k=1", number))

    assert url(state.workflows[0].actions[0]) == SHOWN
    removed = "Removed the credentials of: the webhook URL of action 1."
    assert done.note == (removed if number else None)
    assert done.actions[0].shown_in_part == ()


async def test_an_empty_user_name_is_not_written_where_the_path_holds_an_at() -> None:
    service, state = seeding(hooked(7, "https://u:pw@h.example/@x"))

    _ = await write(service, given("https://@h.example/@x", 7))

    assert url(state.workflows[0].actions[0]) == "https://h.example/@x"


async def test_another_url_never_gets_the_stored_credentials_and_the_result_says_so() -> None:
    service, state = seeding(hooked(7, SECRET), WorkflowAction(id=8, type=6))

    done = await write(
        service,
        given("https://h.example/y", 7),
        given("https://new:n@h.example/z", 7),
        ActionInput(type="trash", id=8),
    )

    actions = state.workflows[0].actions
    assert [url(a) for a in actions[:2]] == ["https://h.example/y", "https://new:n@h.example/z"]
    assert str(done.note).startswith("Removed the credentials of: the webhook URL of action 1. A ")


@pytest.mark.parametrize(
    ("written", "does"),
    [
        (SHOWN, "'h.example', with credentials (not shown)."),
        (SECRET, "'h.example', with credentials (not shown)."),
        (
            "https://@h.example/x?k=1",
            "'h.example', and remove the credentials of the stored URL (not shown).",
        ),
        (
            "https://n.example/x",
            "'n.example', and remove the credentials of the stored URL (not shown).",
        ),
    ],
)
async def test_the_confirmation_says_what_becomes_of_the_credentials(
    written: str, does: str
) -> None:
    service, state = seeding(hooked(7, SECRET), enabled=True)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await write(service, given(written, 7))

    assert raised.value.message.endswith(f"send a POST request to {does}")
    assert "pw" not in raised.value.message
    assert url(state.workflows[0].actions[0]) == SECRET


async def test_hidden_headers_stay_with_a_kept_url() -> None:
    hook: dict[str, JsonValue] = {"url": UNCLEAR, "headers": {"X-Token": "real"}}
    service, state = seeding(WorkflowAction(id=7, type=4, settings={"webhook": hook}))
    sent = WebhookSettings(url=URL_NOT_SHOWN, headers={"X-Token": "(hidden)"})

    _ = await write(service, ActionInput(type=4, id=7, webhook=sent))

    stored = cast("dict[str, JsonValue]", state.workflows[0].actions[0].settings["webhook"])
    assert stored["url"] == UNCLEAR
    assert stored["headers"] == {"X-Token": "real"}
