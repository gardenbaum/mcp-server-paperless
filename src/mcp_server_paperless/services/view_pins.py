"""Which saved views the current user has on the dashboard and in the sidebar (decision-92).

Paperless keeps both as lists of view ids in the UI settings of a user, and writes those only as a
whole. So they are read, changed in these two lists, written back and compared with a fresh read.
Nothing is ever sent twice.
"""

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    Conflict,
    PaperlessMcpError,
    UnexpectedAnswer,
    UpstreamUnavailable,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.views import PinnedView

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from pydantic import JsonValue

    from mcp_server_paperless.domain.ports import UiSettingsReader, UiSettingsWriter
    from mcp_server_paperless.domain.views import SavedView

    type Settings = Mapping[str, JsonValue]

VIEWS: Final = "saved_views"
DASHBOARD: Final = "dashboard_views_visible_ids"
SIDEBAR: Final = "sidebar_views_visible_ids"
MOST_KEYS: Final = 10
LOOK: Final = "Read the view with get_saved_view first; do not repeat the call blindly."
NOT_READ: Final = (
    "The UI settings could not be read again: whether they were stored as sent is not known."
)
NOT_THERE: Final = (
    "The change is not there, but whether it still arrives is not known. If it does, it sets all "
    "UI settings of this user back to what they were before the call, with the change."
)
# Longest wait for another call of this server, in seconds: a call takes three requests.
MOST_WAIT: Final = 40.0


def is_view(entry: JsonValue, view_id: int) -> bool:
    """Whether the web UI takes ``entry`` for the view: a number (it compares with ``===``)."""
    return type(entry) in {int, float} and entry == view_id


def pinned(settings: Settings, place: str, view_id: int) -> bool:
    """Whether the list ``place`` holds the view; as in the web UI, what is no list holds none."""
    views = settings.get(VIEWS)
    row = views.get(place) if isinstance(views, dict) else None
    return isinstance(row, list) and any(is_view(entry, view_id) for entry in row)


def _odd(*path: str) -> Conflict:
    name = ".".join(path)
    message = f"The UI settings hold something unexpected under {name!r}. Nothing was changed."
    return Conflict(message, hint="Let the user pin a view once in the Paperless web UI.")


def with_pins(settings: Settings, view_id: int, wanted: Mapping[str, bool]) -> Settings:
    """``settings`` with the view in or out of each list of ``wanted``, all else as it is.

    Raises:
        Conflict: ``saved_views`` is no object or a list to change is no list.
    """
    stored = settings.get(VIEWS, {})
    if not isinstance(stored, dict):
        raise _odd(VIEWS)
    views = dict(stored)
    for place, shown in wanted.items():
        row = views.get(place, [])
        if not isinstance(row, list):
            raise _odd(VIEWS, place)
        if shown != any(is_view(entry, view_id) for entry in row):
            kept = [entry for entry in row if not is_view(entry, view_id)]
            views[place] = [*kept, view_id] if shown else kept
    return {**settings, VIEWS: views}


def _same(one: JsonValue, other: JsonValue) -> bool:
    """Whether two values are the same, telling true from 1 and 1 from 1.0."""
    return json.dumps(one, sort_keys=True) == json.dumps(other, sort_keys=True)


def differing(expected: Settings, found: Settings, within: str = "") -> list[str]:
    """The keys whose values differ, those of the saved views one by one."""
    names: list[str] = []
    for key in sorted({*expected, *found}):
        one, other = expected.get(key), found.get(key)
        if key in expected and key in found and _same(one, other):
            continue
        if not within and key == VIEWS and isinstance(one, dict) and isinstance(other, dict):
            names += differing(one, other, f"{VIEWS}.")
        else:
            names.append(f"{within}{key}")
    return names


class ViewPins:
    """Reads and writes the pins of the current user."""

    def __init__(
        self, reader: UiSettingsReader, writer: UiSettingsWriter, *, wait: float = MOST_WAIT
    ) -> None:
        """Read through ``reader``, write through ``writer``; wait ``wait`` seconds for a turn."""
        self._reader: UiSettingsReader = reader
        self._writer: UiSettingsWriter = writer
        # Assistants call tools in parallel: two writes at once would undo each other.
        self._one_at_a_time: asyncio.Lock = asyncio.Lock()
        self._wait: float = wait

    async def settings(self) -> Settings | None:
        """The UI settings to look pins up in; ``None`` where they cannot be read."""
        try:
            return await self._reader.get_ui_settings()
        except PaperlessMcpError:
            return None

    async def pin(
        self, view: SavedView, *, dashboard: bool | None, sidebar: bool | None
    ) -> PinnedView:
        """Put the view on the dashboard or into the sidebar, or take it away; null leaves it.

        Raises:
            Validation: Neither is named.
            Conflict: The settings have an unexpected form (nothing was changed), or differ
                afterwards from what was written.
            PermissionDenied: The user may not read or write UI settings.
            PaperlessMcpError: Whether the change was stored is not known.
        """
        wanted = {
            place: shown
            for place, shown in ((DASHBOARD, dashboard), (SIDEBAR, sidebar))
            if shown is not None
        }
        if not wanted:
            message = "Nothing to change: neither dashboard nor sidebar was named."
            raise Validation(message, hint="Pass dashboard, sidebar or both as true or false.")
        async with self._turn():
            before = await self._reader.get_ui_settings()
            after = with_pins(before, view.id, wanted)
            changed = any(pinned(before, p, view.id) != shown for p, shown in wanted.items())
            if changed:
                await self._write(after, view, wanted)
        places = {p: pinned(after, p, view.id) for p in (DASHBOARD, SIDEBAR)}
        where = " and ".join(
            name
            for place, name in ((DASHBOARD, "on the dashboard"), (SIDEBAR, "in the sidebar"))
            if places[place]
        )
        subject = f"saved view {quoted(view.name)} (id {view.id})"
        state = f"{subject} is {where or 'neither on the dashboard nor in the sidebar'}"
        return PinnedView(
            id=view.id,
            name=view.name,
            on_dashboard=places[DASHBOARD],
            in_sidebar=places[SIDEBAR],
            changed=changed,
            message=f"{'Changed' if changed else 'Nothing to change'}: {state} of this user.",
        )

    @contextlib.asynccontextmanager
    async def _turn(self) -> AsyncGenerator[None]:
        """The turn of one call.

        Raises:
            Conflict: Another call took too long; nothing was changed.
        """
        try:
            async with asyncio.timeout(self._wait):
                _ = await self._one_at_a_time.acquire()
        except TimeoutError:
            message = "Another call is still changing UI settings. Nothing was changed."
            raise Conflict(message, hint="Call again in a moment.") from None
        try:
            yield
        finally:
            self._one_at_a_time.release()

    async def _write(self, after: Settings, view: SavedView, wanted: Mapping[str, bool]) -> None:
        """Send the settings once and compare them with a fresh read."""
        lost = ""
        try:
            await self._writer.replace_ui_settings(after)
        except (UpstreamUnavailable, UnexpectedAnswer) as error:
            lost = f"{error.message} "
        try:
            found = await self._reader.get_ui_settings()
        except PaperlessMcpError:
            raise PaperlessMcpError(lost + NOT_READ, hint=LOOK) from None
        as_asked = all(pinned(found, p, view.id) == shown for p, shown in wanted.items())
        if lost and not as_asked:
            raise PaperlessMcpError(lost + NOT_THERE, hint=LOOK)
        names = differing(after, found)
        if not names:
            return
        shown = ", ".join(quoted(name) for name in names[:MOST_KEYS])
        if len(names) > MOST_KEYS:
            shown = f"{shown} and {len(names) - MOST_KEYS} more"
        result = "is stored as asked" if as_asked else "is not as asked"
        message = (
            f"The place of the view {result}, but the UI settings differ from what was sent: "
            f"{shown}. Perhaps someone else saved settings meanwhile (an open Paperless tab)."
        )
        raise Conflict(message, hint=f"Tell the user. {LOOK}")
