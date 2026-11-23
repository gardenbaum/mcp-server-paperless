"""The recording behind the coverage table sees fields, nested fields and values (PLMCP-91)."""

from typing import TYPE_CHECKING, cast

import httpx2
import observation
import pytest
from observation import Recorder, leaves, recorded, request_fields, scalar_text

from mcp_server_paperless.server import Ports

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def test_the_paths_of_a_nested_body_name_objects_and_lists() -> None:
    paths, values = leaves(
        {"name": "w", "triggers": [{"type": 1, "filter": {"path": "x"}}], "on": True}
    )

    assert paths == {
        "name",
        "triggers",
        "triggers[].type",
        "triggers[].filter",
        "triggers[].filter.path",
        "on",
    }
    assert values == {"name=w", "triggers[].type=1", "triggers[].filter.path=x", "on=true"}


def test_a_scalar_list_has_one_path_for_its_items() -> None:
    paths, values = leaves({"documents": [1, 2]})

    assert paths == {"documents"}
    assert values == {"documents[]=1", "documents[]=2"}


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (True, "true"),
        (False, "false"),
        (3, "3"),
        ("a", "a"),
        ("x" * 81, None),
        (1.5, None),
        (None, None),
    ],
)
def test_only_short_strings_numbers_and_flags_are_values(value: object, text: str | None) -> None:
    assert scalar_text(value) == text


def test_a_json_request_gives_query_names_body_paths_and_values() -> None:
    request = httpx2.Request(
        "POST", "http://p.test/api/x/?page=2&page=3", json={"a": {"b": "c"}, "list": [{"d": 1}]}
    )

    query, body, values = request_fields(request)

    assert query == {"page"}
    assert body == {"a", "a.b", "list", "list[].d"}
    assert values == {"query:page=2", "query:page=3", "body:a.b=c", "body:list[].d=1"}


def test_a_form_gives_its_field_names_and_the_text_of_the_plain_ones() -> None:
    request = httpx2.Request(
        "POST", "http://p.test/api/x/", data={"title": "T"}, files={"document": ("a.pdf", b"%PDF")}
    )
    _ = request.read()

    _, body, values = request_fields(request)

    assert body == {"title", "document"}
    assert values == {"body:title=T"}


def test_a_request_without_a_body_has_no_fields() -> None:
    assert request_fields(httpx2.Request("GET", "http://p.test/api/x/")) == (
        frozenset(),
        frozenset(),
        frozenset(),
    )


class Port:
    """A port with one method and one plain attribute."""

    label: str = "thing"

    async def get(self, number: int, *, full: bool = False) -> str:
        return f"{number}{full}"


@pytest.mark.anyio
async def test_a_recorded_port_notes_the_call_and_the_tool_and_still_answers() -> None:
    start = len(observation.PORT_CALLS)
    token = observation.CURRENT_TOOL.set("get_thing")
    try:
        get = cast("Callable[..., Awaitable[str]]", Recorder("things", Port()).get)
        found = await get(3, full=True)
    finally:
        observation.CURRENT_TOOL.reset(token)

    assert found == "3True"
    assert observation.PORT_CALLS[start:] == [
        observation.PortCall("get_thing", "things", "get", (3,), {"full": True})
    ]


def test_a_recorder_hands_on_plain_attributes() -> None:
    assert Recorder("things", Port()).label == "thing"


def test_ports_are_left_alone_while_nothing_is_recorded() -> None:
    ports = object.__new__(Ports)

    assert recorded(ports) is ports
