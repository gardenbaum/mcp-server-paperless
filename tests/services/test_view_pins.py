"""ViewPins: dashboard and sidebar of the current user, written back with everything else."""

import asyncio
import copy
import json
from dataclasses import replace
from typing import TYPE_CHECKING, cast, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeSavedView, seeded
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.errors import (
    Conflict,
    PaperlessMcpError,
    PermissionDenied,
    UnexpectedAnswer,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.views import ViewCreate
from mcp_server_paperless.services.view_pins import ViewPins
from services.test_views import Setup

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from mcp_server_paperless.domain.views import SavedView

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-92")]

type Settings = dict[str, JsonValue]
type Asked = tuple[bool | None, bool | None]  # the dashboard and the sidebar

DASHBOARD, SIDEBAR = "dashboard_views_visible_ids", "sidebar_views_visible_ids"


@final
class Store(FakePaperless):
    """A fake whose answers to a write can be lost, and where others can write in between."""

    def __init__(self, stored: Settings | None = None) -> None:
        super().__init__(seeded(), user=ADMIN[0], allow_writes=True)
        self.state.ui_settings[self.user.id] = stored or {}
        self.writes: list[Settings] = []
        self.reads: int = 0
        self.after_write: Callable[[Settings], None] = lambda _settings: None
        self.fails: PaperlessMcpError | None = None
        self.applied: bool = True
        self.reading_fails_from: int | None = None
        self.reading_fails: PaperlessMcpError = UpstreamUnavailable("down")
        self.before_read: Callable[[], None] = lambda: None
        self.hold: asyncio.Event | None = None

    @property
    def stored(self) -> Settings:
        return self.state.ui_settings[self.user.id]

    @override
    async def get_ui_settings(self) -> Settings:
        self.reads += 1
        if self.reading_fails_from is not None and self.reads >= self.reading_fails_from:
            raise self.reading_fails
        self.before_read()
        if self.hold is not None:
            _ = await self.hold.wait()
        return await super().get_ui_settings()

    @override
    async def replace_ui_settings(self, settings: Mapping[str, JsonValue]) -> None:
        self.writes.append(copy.deepcopy(dict(settings)))
        await asyncio.sleep(0)
        if self.applied:
            await super().replace_ui_settings(settings)
        self.after_write(self.stored)
        if self.fails is not None:
            raise self.fails

    async def view(self) -> SavedView:
        return await self.get_saved_view(1)

    @property
    def pins(self) -> ViewPins:
        return ViewPins(self, self)


def saved(dashboard: list[JsonValue], sidebar: list[JsonValue], **more: JsonValue) -> Settings:
    return {"saved_views": {DASHBOARD: dashboard, SIDEBAR: sidebar, **more}}


@pytest.mark.parametrize(
    ("before", "asked", "after"),
    [
        ({}, (True, None), {"saved_views": {DASHBOARD: [1]}}),
        ({}, (True, True), saved([1], [1])),
        (saved([], []), (None, True), saved([], [1])),
        (saved([5, 9], [7]), (True, True), saved([5, 9, 1], [7, 1])),
        (saved([5, 1, 9], [1]), (False, None), saved([5, 9], [1])),
        (saved([5, 1, 9], [1, 2]), (False, False), saved([5, 9], [2])),
        (saved([1, 1], []), (False, None), saved([], [])),
        (saved([1], []), (False, True), saved([], [1])),
        (saved([1, 5], []), (True, True), saved([1, 5], [1])),
        # Other entries keep their type and place; what the web UI does not count is no pin.
        (saved(["1", True, None, 4], []), (True, None), saved(["1", True, None, 4, 1], [])),
        (saved([1.0, "1", True], []), (False, None), saved(["1", True], [])),
        (saved([3], [], sort=[3]), (True, None), saved([3, 1], [], sort=[3])),
    ],
)
async def test_the_lists_change_as_asked_and_nothing_else_does(
    before: Settings, asked: Asked, after: Settings
) -> None:
    dashboard, sidebar = asked
    other: Settings = {"language": "de-de", "nested": {"a": [1, None]}}
    store = Store({**other, **copy.deepcopy(before)})

    done = await store.pins.pin(await store.view(), dashboard=dashboard, sidebar=sidebar)

    assert json.dumps(store.stored, sort_keys=True) == json.dumps(
        {**other, **after}, sort_keys=True
    )
    assert len(store.writes) == 1
    assert (done.id, done.name, done.changed) == (1, "e2e-view", True)
    expected = {"on_dashboard": dashboard, "in_sidebar": sidebar}
    shown = done.model_dump()
    assert all(shown[name] is value for name, value in expected.items() if value is not None)


@pytest.mark.parametrize(
    ("before", "asked"),
    [
        (saved([1], []), (True, None)),
        (saved([1], []), (True, False)),
        (saved([1.0], []), (True, None)),
        ({}, (False, False)),
        (saved(["1"], []), (False, None)),
    ],
)
async def test_what_already_is_as_asked_is_not_sent(before: Settings, asked: Asked) -> None:
    dashboard, sidebar = asked
    store = Store(before)

    done = await store.pins.pin(await store.view(), dashboard=dashboard, sidebar=sidebar)

    assert store.writes == []
    assert done.changed is False
    assert (done.on_dashboard, done.in_sidebar) == (bool(dashboard), bool(sidebar))


async def test_one_of_the_two_must_be_named() -> None:
    store = Store()

    with pytest.raises(Validation, match="Nothing to change"):
        _ = await store.pins.pin(await store.view(), dashboard=None, sidebar=None)
    assert store.reads == 0


@pytest.mark.parametrize(
    ("odd", "named"),
    [
        ({"saved_views": []}, "saved_views"),
        ({"saved_views": None}, "saved_views"),
        ({"saved_views": "text"}, "saved_views"),
        ({"saved_views": {DASHBOARD: None}}, DASHBOARD),
        ({"saved_views": {DASHBOARD: {"1": 1}}}, DASHBOARD),
        ({"saved_views": {DASHBOARD: [], SIDEBAR: "1"}}, SIDEBAR),
    ],
)
async def test_settings_of_another_form_are_refused_and_not_repaired(
    odd: Settings, named: str
) -> None:
    store = Store(copy.deepcopy(odd))
    sidebar = named == SIDEBAR

    with pytest.raises(Conflict, match=named) as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=sidebar or None)

    assert store.writes == []
    assert store.stored == odd
    assert "Nothing was changed" in raised.value.message


async def test_a_list_that_is_not_asked_about_may_have_any_form() -> None:
    store = Store({"saved_views": {SIDEBAR: "odd"}})

    done = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert store.stored == {"saved_views": {SIDEBAR: "odd", DASHBOARD: [1]}}
    assert (done.on_dashboard, done.in_sidebar) == (True, False)


def sent(**more: JsonValue) -> Settings:
    return {"language": "de-de", **saved([1], [1]), **more}


@pytest.mark.parametrize(
    ("found", "named"),
    [
        (sent(language="fr-secret"), "'language'"),
        (sent(theme="secret"), "'theme'"),
        (sent(theme=None), "'theme'"),
        (saved([1], [1]), "'language'"),
        (sent(**saved([1], [1], sort=["secret"])), "'saved_views.sort'"),
        (sent(**saved([1, 77], [1])), f"'saved_views.{DASHBOARD}'"),
        (sent(**saved([1], [True])), f"'saved_views.{SIDEBAR}'"),
        (sent(saved_views="secret"), "'saved_views'"),
    ],
    ids=["changed", "added", "null", "removed", "order", "dashboard", "sidebar", "no object"],
)
async def test_what_differs_afterwards_is_named_by_key_only(found: Settings, named: str) -> None:
    store = Store({"language": "de-de", **saved([], [1])})

    def other(stored: Settings) -> None:
        stored.clear()
        stored.update(found)

    store.after_write = other

    with pytest.raises(Conflict) as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    text = raised.value.describe()
    assert named in text
    assert text.count("'") == 2  # one key, and no other
    assert "secret" not in text
    assert "someone else" in text
    assert len(store.writes) == 1


@pytest.mark.parametrize("value", [1, 1.0, "true"])
async def test_a_value_of_another_type_is_a_difference(value: JsonValue) -> None:
    store = Store({"flag": True})
    store.after_write = lambda s: s.update({"flag": value})

    with pytest.raises(Conflict, match="'flag'"):
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)


async def test_a_pin_that_is_gone_again_is_said() -> None:
    store = Store(saved([5], []))
    store.after_write = lambda s: s.update(saved([5], []))

    with pytest.raises(Conflict, match="not as asked") as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert len(store.writes) == 1
    assert raised.value.hint is not None


async def test_many_and_hostile_keys_stay_short() -> None:
    store = Store()
    store.after_write = lambda s: s.update({f"{n:02}{'x' * 500}\n": 1 for n in range(40)})

    with pytest.raises(Conflict) as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert "\n" not in raised.value.message
    assert len(raised.value.message) < 1200
    assert "and 30 more" in raised.value.message


async def test_ten_keys_are_named_in_full() -> None:
    store = Store()
    store.after_write = lambda s: s.update({f"k{n}": 1 for n in range(10)})

    with pytest.raises(Conflict) as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert "'k9'" in raised.value.message
    assert "more" not in raised.value.message


async def test_a_key_of_the_same_name_inside_the_saved_views_is_named_with_its_place() -> None:
    nested: Settings = {"a": 1}
    store = Store({"saved_views": {"saved_views": nested}})

    def other(stored: Settings) -> None:
        stored["saved_views"] = {DASHBOARD: [1], "saved_views": {"a": 2}}

    store.after_write = other

    with pytest.raises(Conflict, match=r"'saved_views\.saved_views'"):
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)


async def test_a_lost_answer_of_which_only_a_half_is_there_is_not_known() -> None:
    store = Store(saved([1], []))
    store.fails, store.applied = UpstreamTimeout("slow"), False

    with pytest.raises(PaperlessMcpError, match="not known"):
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=True)


@pytest.mark.parametrize(
    ("before", "asked", "message"),
    [
        ({}, (True, None), "Changed: {} is on the dashboard of this user."),
        ({}, (None, True), "Changed: {} is in the sidebar of this user."),
        (saved([1], []), (None, True), "Changed: {} is on the dashboard and in the sidebar of"),
        (saved([1], []), (True, None), "Nothing to change: {} is on the dashboard of this user."),
        (
            saved([1], []),
            (False, None),
            "Changed: {} is neither on the dashboard nor in the sidebar of this user.",
        ),
    ],
)
async def test_the_message_says_where_the_view_is(
    before: Settings, asked: Asked, message: str
) -> None:
    store = Store(before)
    dashboard, sidebar = asked

    done = await store.pins.pin(await store.view(), dashboard=dashboard, sidebar=sidebar)

    assert done.message.startswith(message.format("saved view 'e2e-view' (id 1)"))


@pytest.mark.parametrize("lost", [UpstreamTimeout("slow"), UpstreamUnavailable("reset")])
async def test_a_lost_answer_is_looked_up_once_and_never_repeated(lost: PaperlessMcpError) -> None:
    store = Store()
    store.fails = lost

    done = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert (done.on_dashboard, done.changed, len(store.writes)) == (True, True, 1)


async def test_a_lost_answer_without_the_change_is_not_known_to_have_failed() -> None:
    store = Store()
    store.fails, store.applied = UpstreamTimeout("slow"), False

    with pytest.raises(PaperlessMcpError, match="not known") as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert (len(store.writes), store.reads) == (1, 2)
    assert raised.value.hint is not None
    assert "get_saved_view" in raised.value.hint


@pytest.mark.parametrize("lost", [None, UpstreamTimeout("slow")])
async def test_settings_that_cannot_be_read_again_leave_it_unknown(
    lost: PaperlessMcpError | None,
) -> None:
    store = Store()
    store.fails, store.reading_fails_from = lost, 2

    with pytest.raises(PaperlessMcpError, match="could not be read again"):
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert len(store.writes) == 1


@pytest.mark.parametrize("refusal", [PermissionDenied("no"), Validation("bad")])
async def test_a_refused_write_is_reported_as_it_is(refusal: PaperlessMcpError) -> None:
    store = Store()
    store.fails, store.applied = refusal, False

    with pytest.raises(type(refusal)) as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert raised.value is refusal
    assert store.reads == 1


async def test_parallel_calls_of_one_server_do_not_undo_each_other() -> None:
    store = Store()
    store.state.saved_views.append(FakeSavedView(id=2, name="second", owner=2))
    pins = store.pins

    _ = await asyncio.gather(
        pins.pin(await store.get_saved_view(1), dashboard=True, sidebar=None),
        pins.pin(await store.get_saved_view(2), dashboard=True, sidebar=True),
    )

    assert store.stored == saved([1, 2], [2])


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(), inner, max_size=3),
    max_leaves=10,
)
LISTS = st.lists(st.integers(-2, 5) | st.text(max_size=2) | st.none() | st.booleans(), max_size=5)


@given(
    other=st.dictionaries(st.text().filter(lambda k: k != "saved_views"), JSON, max_size=4),
    places=st.dictionaries(st.sampled_from([DASHBOARD, SIDEBAR]), LISTS),
    rest=st.dictionaries(st.text().filter(lambda k: k not in {DASHBOARD, SIDEBAR}), JSON),
    asked=st.tuples(st.booleans() | st.none(), st.booleans() | st.none()),
)
async def test_whatever_is_stored_only_the_two_lists_change(
    other: Settings,
    places: dict[str, list[JsonValue]],
    rest: Settings,
    asked: Asked,
) -> None:
    before: Settings = {**other, "saved_views": {**rest, **places}}
    store = Store(copy.deepcopy(before))
    dashboard, sidebar = asked
    if asked == (None, None):
        return

    _ = await store.pins.pin(await store.view(), dashboard=dashboard, sidebar=sidebar)

    after = copy.deepcopy(store.stored)
    for settings in (before, after):
        views = cast("dict[str, JsonValue]", settings["saved_views"])
        for name in (DASHBOARD, SIDEBAR):
            row = cast("list[JsonValue]", views.pop(name, []))
            views[f"others {name}"] = [entry for entry in row if not _is_view(entry)]
    assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)


def _is_view(entry: JsonValue) -> bool:
    return isinstance(entry, int) and not isinstance(entry, bool) and entry == 1


async def test_views_say_where_the_current_user_sees_them() -> None:
    setup = Setup()
    admin = setup.fake.user.id
    setup.state.ui_settings[admin] = {**saved([1], []), "other": 1}

    [listed] = (await setup.views.list()).views
    read = await setup.views.get(1)

    assert (listed.on_dashboard, listed.in_sidebar) == (True, False)
    assert read == listed


@pytest.mark.parametrize("odd", [{}, {"saved_views": "x"}, {"saved_views": {DASHBOARD: "1"}}])
async def test_odd_settings_show_a_view_nowhere_as_the_web_ui_does(odd: Settings) -> None:
    setup = Setup()
    setup.state.ui_settings[setup.fake.user.id] = odd

    view = await setup.views.get(1)

    assert (view.on_dashboard, view.in_sidebar) == (False, False)


async def test_without_the_permission_it_is_not_known_and_nothing_fails() -> None:
    setup = Setup(READER[0], grant=("view_savedview", "add_savedview"))
    reader = setup.fake.user
    setup.fake.user = replace(reader, permissions=reader.permissions - {"view_uisettings"})

    made = await setup.views.create(ViewCreate(name="mine"))
    [listed] = (await setup.views.list()).views

    assert (made.on_dashboard, made.in_sidebar) == (None, None)
    assert listed == made


async def test_a_view_one_may_only_see_can_be_pinned() -> None:
    setup = Setup(READER[0], grant=("view_savedview", "add_uisettings"))
    setup.view("e2e-view").viewers.add(setup.fake.user.id)

    done = await setup.views.pin("E2E-VIEW", dashboard=None, sidebar=True)

    assert (done.id, done.on_dashboard, done.in_sidebar, done.changed) == (1, False, True, True)
    assert setup.state.ui_settings[setup.fake.user.id] == {"saved_views": {SIDEBAR: [1]}}
    assert (await setup.views.get(1)).can_change is False


@pytest.mark.parametrize("error", [PermissionDenied("Nothing was changed."), Validation("odd")])
async def test_a_refused_second_read_does_not_say_that_nothing_was_changed(
    error: PaperlessMcpError,
) -> None:
    store = Store()
    store.reading_fails_from, store.reading_fails = 2, error

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert "could not be read again" in raised.value.message
    assert "Nothing was changed" not in raised.value.describe()
    assert store.stored == {"saved_views": {DASHBOARD: [1]}}


async def test_a_difference_elsewhere_says_that_the_pin_is_stored() -> None:
    store = Store()
    store.after_write = lambda s: s.update({"theme": 1})

    with pytest.raises(Conflict, match="The place of the view is stored as asked, but"):
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)


async def test_a_change_that_is_not_there_may_still_come_and_set_everything_back() -> None:
    store = Store()
    store.fails, store.applied = UpstreamTimeout("Paperless did not respond in time."), False

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert raised.value.message.startswith("Paperless did not respond in time. ")
    assert "sets all UI settings of this user back to what they were before the call" in (
        raised.value.message
    )


@pytest.mark.parametrize("applied", [True, False])
async def test_an_answer_that_is_not_paperlesss_is_looked_up_and_named(
    applied: bool,  # noqa: FBT001 -- the case
) -> None:
    store = Store()
    page = "Paperless sent an unexpected response for POST /api/ui_settings/."
    store.fails, store.applied = UnexpectedAnswer(page), applied

    if applied:
        done = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)
        assert done.changed
        return
    with pytest.raises(PaperlessMcpError, match="unexpected response for POST"):
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)
    assert len(store.writes) == 1


async def test_both_failures_are_named_when_the_settings_cannot_be_read_again() -> None:
    store = Store()
    store.fails, store.reading_fails_from = UnexpectedAnswer("A login page came."), 2

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await store.pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert raised.value.message.startswith("A login page came. The UI settings could not be read")


async def test_a_call_does_not_wait_for_ever_for_another_one() -> None:
    store = Store()
    pins = ViewPins(store, store, wait=0.01)
    view = await store.view()
    held, go = asyncio.Event(), asyncio.Event()
    store.before_read = held.set
    store.hold = go
    first = asyncio.create_task(pins.pin(view, dashboard=True, sidebar=None))
    _ = await held.wait()

    with pytest.raises(Conflict, match="Another call") as raised:
        _ = await pins.pin(view, dashboard=None, sidebar=True)
    go.set()
    _ = await first
    done = await pins.pin(view, dashboard=None, sidebar=True)

    assert "Nothing was changed" in raised.value.message
    assert done.changed


async def test_a_failed_call_lets_the_next_one_through() -> None:
    store = Store({"saved_views": "odd"})
    pins = store.pins
    with pytest.raises(Conflict):
        _ = await pins.pin(await store.view(), dashboard=True, sidebar=None)
    store.state.ui_settings[store.user.id] = {}

    done = await pins.pin(await store.view(), dashboard=True, sidebar=None)

    assert done.changed
