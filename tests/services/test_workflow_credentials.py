"""Webhook URLs without their credentials, and what writing them back does."""

import time
from typing import TYPE_CHECKING, cast, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.errors import ConfirmationRequired, PermissionDenied, Validation
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
OF_THE_STORED = "the credentials of the stored URL (not shown)."
POSSIBLY = "with, or possibly with,"
OR_REPLACE = f"remove or replace {OF_THE_STORED}"
UNCLEAR = "https://us/er:pw@h.example/x"


class CountingReader(FakePaperless):
    lists: int = 0

    @override
    async def list_workflows(self) -> tuple[Workflow, ...]:
        self.lists += 1
        return await super().list_workflows()


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


@pytest.mark.parametrize("number", [None, 8, 9])
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
    assert done.note == (
        "Removed the credentials of: the webhook URL of action 1."
        if number
        else "Removed the stored credentials of 1 webhook URL that no action kept by its id."
    )
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
    assert str(done.note).startswith(
        "Removed the credentials of: the webhook URL of action 1. Replaced the credentials of: "
    )
    assert "of: the webhook URL of action 2. A " in str(done.note)


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
        (
            "https://new:n@n.example/x",
            f"'n.example', with credentials (not shown), and replace {OF_THE_STORED}",
        ),
        (
            "https://n.example/@x",
            f"'n.example', {POSSIBLY} credentials (not shown), and {OR_REPLACE}",
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


NAME = "https://h.example/@name"


@pytest.mark.parametrize(
    ("written", "does"),
    [
        (URL_NOT_SHOWN, "'h.example', with, or possibly with, credentials (not shown)."),
        (
            "https://n.example/x",
            "'n.example', and remove any credentials of the stored URL (not shown).",
        ),
    ],
)
async def test_the_confirmation_does_not_claim_credentials_that_cannot_be_told(
    written: str, does: str
) -> None:
    service, _ = seeding(hooked(7, NAME), enabled=True)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await write(service, given(written, 7))

    assert raised.value.message.endswith(f"send a POST request to {does}")


async def test_the_note_does_not_claim_credentials_that_cannot_be_told() -> None:
    service, _ = seeding(hooked(7, NAME))

    done = await write(service, given("https://n.example/x", 7))

    assert done.note == "Removed any credentials of: the webhook URL of action 1."


SPELLINGS = pytest.mark.parametrize(
    "spelling", [f" {SHOWN}", f"{SHOWN}\n", "HTTPS://H.Example/x?k=1"]
)


@SPELLINGS
async def test_another_spelling_of_the_shown_url_is_refused_without_an_id(spelling: str) -> None:
    service, state = seeding(hooked(7, SECRET))
    before = state.workflows[0]

    with pytest.raises(Validation, match="The webhook URL of action 1 is a stored one"):
        _ = await write(service, given(spelling), confirm=True)

    assert state.workflows[0] == before


@SPELLINGS
async def test_another_spelling_of_the_shown_url_keeps_what_its_id_stored(spelling: str) -> None:
    service, state = seeding(hooked(7, SECRET))

    done = await write(service, given(spelling, 7))

    assert url(state.workflows[0].actions[0]) == SECRET
    assert done.note == "Kept as stored, with any credentials: the webhook URL of action 1."


async def test_a_path_in_other_letters_is_another_url() -> None:
    service, state = seeding(hooked(7, SECRET))

    _ = await write(service, given("https://h.example/X?k=1", 7))

    assert url(state.workflows[0].actions[0]) == "https://h.example/X?k=1"


async def test_the_spelling_of_a_url_without_credentials_can_be_changed() -> None:
    service, state = seeding(hooked(7, "https://H.example/x"))

    _ = await write(service, given("https://h.example/x", 7))

    assert url(state.workflows[0].actions[0]) == "https://h.example/x"


@pytest.mark.parametrize("written", ["https://h.example/y", SHOWN, URL_NOT_SHOWN, SECRET])
@pytest.mark.parametrize("stored", [(), (hooked(7, SECRET),)])
async def test_a_webhook_with_an_id_that_is_not_in_the_workflow_is_refused(
    written: str, stored: tuple[WorkflowAction, ...]
) -> None:
    service, state = seeding(*stored)
    before = state.workflows[0]

    with pytest.raises(Validation) as raised:
        _ = await write(service, given(written, 99), confirm=True)

    assert "action 1" in raised.value.message
    assert raised.value.hint == (
        "Send the id the action has in get_workflow now, or leave the id out for a new webhook."
    )
    assert "pw" not in f"{raised.value.message}{raised.value.hint}"
    assert state.workflows[0] == before


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    ("stored", "gone"),
    [
        ([SECRET], "the stored credentials of 1 webhook URL"),
        ([SECRET, OTHER], "the stored credentials of 2 webhook URLs"),
        ([SECRET, NAME], "any stored credentials of 2 webhook URLs"),
    ],
)
@pytest.mark.parametrize("number", [None, 1])
async def test_stored_credentials_that_no_action_claims_do_not_vanish_silently(
    stored: list[str], gone: str, number: int | None, *, enabled: bool
) -> None:
    actions = [hooked(n, u) for n, u in enumerate(stored, start=7)]
    service, state = seeding(
        WorkflowAction(id=1, type=6), *actions, hooked(20, SECRET), enabled=enabled
    )
    sent = (given("https://n.example/y", number), given(SHOWN, 20))

    if enabled:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await write(service, *sent)
        assert raised.value.message.endswith(f"; remove {gone} that no action keeps (not shown).")
    done = await write(service, *sent, confirm=True)

    assert str(done.note).startswith(f"Removed {gone} that no action kept by its id. Kept as ")
    assert [url(a) for a in state.workflows[0].actions] == ["https://n.example/y", SECRET]


@pytest.mark.parametrize(
    "sent",
    [
        (given("https://n.example/y", 8),),  # action 7 is left out on purpose
        (given(SECRET), given(SHOWN, 8)),  # its URL is given in full
        (ActionInput(type="trash"),),
    ],
)
async def test_nothing_is_said_where_no_webhook_could_be_the_stored_one(
    sent: tuple[ActionInput, ...],
) -> None:
    service, _ = seeding(hooked(7, SECRET), hooked(8, SHOWN))

    if sent[-1].id == 8 and len(sent) == 2:
        service, _ = seeding(hooked(7, SECRET), hooked(8, OTHER))
    done = await write(service, *sent)

    assert "Removed" not in str(done.note)


@pytest.mark.parametrize("first", [SECRET, SHOWN])
@pytest.mark.parametrize("second", [SECRET, SHOWN])
async def test_one_id_on_two_actions_is_refused_in_whatever_form_the_url_comes(
    first: str, second: str
) -> None:
    service, state = seeding(hooked(7, SECRET))
    before = state.workflows[0]

    with pytest.raises(Validation, match="actions 1, 2 have the id 7"):
        _ = await write(service, given(first, 7), given(second, 7))

    assert state.workflows[0] == before


@pytest.mark.parametrize("empty", ["https://@@", "https://:@:@", " https://@"])
async def test_no_form_of_an_empty_user_name_is_written(empty: str) -> None:
    service, state = seeding()

    _ = await service.create(
        WorkflowCreate(name="n", actions=(given(f"{empty}h.example/x"),), enabled=False),
        confirm=False,
    )

    assert url(state.workflows[-1].actions[0]) == "https://h.example/x"


async def test_an_encoded_at_in_the_host_is_not_repeated_by_a_refusal() -> None:
    service, _ = seeding(hooked(7, "https://s3cret%40h.example/x"))

    with pytest.raises(Validation) as raised:
        _ = await service.update("w", WorkflowEdit(enabled=True), confirm=False)

    assert "s3cret" not in raised.value.message


NOT_COPIED = (
    "Written without the credentials that another workflow stores for this URL (never copied): "
    "the webhook URL of action 1."
)
COPY = pytest.mark.parametrize("copy", [SHOWN, f"{SHOWN} ", "HTTPS://H.example/x?k=1"])


@COPY
async def test_a_copy_in_a_new_workflow_is_written_without_credentials_and_says_so(
    copy: str,
) -> None:
    service, state = seeding(hooked(7, SECRET))
    spec = WorkflowCreate(name="n", actions=(given(copy, 7), given("https://n.example/x")))

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await service.create(spec, confirm=False)
    made = await service.create(spec, confirm=True)

    first, second = raised.value.message.split("; send a POST request to ")[-2:]
    assert first.endswith(
        "'h.example', without the credentials that another workflow stores for this URL"
    )
    assert second == "'n.example'."
    assert "'w'" not in raised.value.message
    assert made.note == NOT_COPIED
    assert url(state.workflows[-1].actions[0]) == copy
    assert url(state.workflows[0].actions[0]) == SECRET


@COPY
@pytest.mark.parametrize("number", [None, 8])
async def test_a_copy_in_another_workflow_is_written_without_credentials_and_says_so(
    copy: str, number: int | None
) -> None:
    service, state = seeding(hooked(8, "https://old.example/x"), enabled=True)
    other = state.workflows[0].model_copy(
        update={"id": 2, "name": "other", "actions": (hooked(7, SECRET),)}
    )
    state.workflows.append(other)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await write(service, given(copy, number))
    done = await write(service, given(copy, number), confirm=True)

    assert raised.value.message.endswith(
        "'h.example', without the credentials that another workflow stores for this URL."
    )
    assert "other" not in raised.value.message.replace("another", "")
    assert done.note == NOT_COPIED
    assert url(state.workflows[0].actions[0]) == copy
    assert state.workflows[1] == other


@pytest.mark.parametrize("empty", ["", "@"])
async def test_a_url_that_is_not_new_or_asks_for_no_credentials_is_no_copy(empty: str) -> None:
    service, state = seeding(hooked(8, SHOWN))
    state.workflows.append(
        state.workflows[0].model_copy(
            update={"id": 2, "name": "other", "actions": (hooked(7, SECRET),)}
        )
    )

    done = await write(service, given(SHOWN.replace("//", f"//{empty}"), 8))

    assert done.note is None


async def test_a_copy_of_a_url_that_is_not_shown_is_refused() -> None:
    service, state = seeding(hooked(7, UNCLEAR))
    spec = WorkflowCreate(name="n", actions=(given(URL_NOT_SHOWN, 7),), enabled=False)

    with pytest.raises(Validation, match="The webhook URL of action 1"):
        _ = await service.create(spec, confirm=True)

    assert len(state.workflows) == 1


async def test_a_copy_needs_no_further_read() -> None:
    service, state = seeding(hooked(7, SECRET))
    fake = CountingReader(state, user=ADMIN[0], allow_writes=True)
    resolver = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=3600.0)
    service = WorkflowService(fake, fake, resolver)

    _ = await service.create(
        WorkflowCreate(name="n", actions=(given(SHOWN),), enabled=False), confirm=True
    )
    _ = await service.update("n", WorkflowEdit(actions=(given(SHOWN),)), confirm=True)

    assert fake.lists == 2


async def test_a_user_who_may_not_see_workflows_gets_no_copy_and_learns_nothing() -> None:
    service, state = seeding(hooked(7, SECRET))
    fake = FakePaperless(state, user=READER[0], allow_writes=True)
    service = WorkflowService(fake, fake, TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=1.0))

    with pytest.raises(PermissionDenied) as raised:
        _ = await service.create(WorkflowCreate(name="n", actions=(given(SHOWN),)), confirm=True)

    assert "credentials" not in raised.value.message
    assert len(state.workflows) == 1


async def test_a_url_without_a_host_is_compared_in_linear_time() -> None:
    service, _ = seeding(hooked(7, SECRET))
    hook = WebhookSettings.model_construct(url="a" * 100_000)
    started = time.process_time()

    with pytest.raises(Validation):
        _ = await write(service, ActionInput.model_construct(type="webhook", webhook=hook))

    assert time.process_time() - started < 5  # quadratic would take much longer


@pytest.mark.parametrize("stored", ["https://@h.example/x", "https://:@h.example/x"])
async def test_an_empty_user_name_alone_is_not_called_credentials(stored: str) -> None:
    plain = "https://h.example/x"
    service, state = seeding(hooked(7, stored), enabled=True)

    for sent in (
        given(plain, 7),
        given(plain),
        given("https://n.example/x", 7),
        given("https://n.example/x"),
    ):
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await write(service, sent)
        assert "redential" not in raised.value.message
    state.workflows.append(
        state.workflows[0].model_copy(update={"id": 2, "name": "other", "enabled": False})
    )
    made = await service.create(WorkflowCreate(name="n", actions=(given(plain),)), confirm=True)
    changed = await write(service, given("https://n.example/x", 7), confirm=True)

    assert (made.note, changed.note) == (None, None)


async def test_an_unclear_url_after_an_empty_user_name_may_hold_credentials() -> None:
    service, _ = seeding(hooked(7, "https://@us/er:pw@h.example/x"))

    done = await write(service, given("https://n.example/x", 7))

    assert done.note == "Removed any credentials of: the webhook URL of action 1."
