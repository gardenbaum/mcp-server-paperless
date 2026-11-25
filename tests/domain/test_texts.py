"""Lone surrogates: where they are in arguments, and a message without them."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.texts import REFUSED_TEXT, parseable, refused_text

BAD = "a\ud800b"


PLACES: list[tuple[object, str | None]] = [
    ({"title": BAD}, "title"),
    ({"title": "\udfff"}, "title"),
    ({"a": "\U0001f9fe", "b": 1, "c": None, "d": ["x", {"y": BAD}]}, "d.1.y"),
    ({"a": {"b": {BAD: 1}}}, "name of a value in a.b"),
    ({"a": [{BAD: 1}]}, "name of a value in a.0"),
    ({BAD: 1}, "name of an argument"),
    ({"a": "\U0001f9fe \ufffd \x00", "b": [1.5, True, None, []]}, None),
    (None, None),
]


@pytest.mark.parametrize(("value", "place"), PLACES)
def test_the_place_of_a_lone_surrogate_is_named(value: object, place: str | None) -> None:
    assert refused_text(value) == place


def message(arguments: object, method: str = "tools/call", **more: object) -> bytes:
    params = {"name": "add_note", "arguments": arguments, **more}
    return json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": params}).encode()


def test_a_call_says_only_where_its_text_was() -> None:
    line = message({"note": BAD, "other": "kept nowhere"}, _meta={"x": "\udc00", "y": "\u20ac"})

    found = parseable(line + b"\n")

    assert found.endswith(b"\n")
    assert json.loads(found) == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "add_note",
            "arguments": {REFUSED_TEXT: "note"},
            "_meta": {"x": "\ufffd", "y": "\u20ac"},
        },
    }


@pytest.mark.parametrize(
    "line",
    [
        message({"note": "\U0001f9fe \ud83e\uddfe"}),
        message({"note": r"\ud800 \\ud800"}),
        '{"note": "\U0001f9fe \u20ac"}'.encode(),
        b'{"note": "\\\\ud800"}\n',
        b"\xff\xfe no json \\x",
        b"",
        b"\n",
    ],
)
def test_a_line_without_a_lone_surrogate_stays_as_it_is(line: bytes) -> None:
    assert parseable(line) == line


@pytest.mark.parametrize(
    ("line", "found"),
    [
        (
            b'{"params": {"uri": "\\uD800\\ud83e\\uddfe"}}',
            b'{"params": {"uri": "R\\ud83e\\uddfe"}}',
        ),
        (b'{"params": {"uri": "\\udc00\\\\\\udbff"}}', b'{"params": {"uri": "R\\\\R"}}'),
        (
            b'{"params": {"uri": "\\ud83e\\ud83e\\uddfe"}}',
            b'{"params": {"uri": "R\\ud83e\\uddfe"}}',
        ),
        (b'{"params": {"arguments": {"a": "x"}, "b": "\\ud800"}}', None),
        (b'{"params": ["\\udc00\\udc01"]}', b'{"params": ["RR"]}'),
        (b'{"params": ["\\ud800"]}', b'{"params": ["R"]}'),
        (b'["\\ud800"]', b'["R"]'),
        (b'{"id": "\\ud800"}', b'{"id": "R"}'),
        (b"no json \\ud800\n", b"no json R\n"),
        (b'{"params": \xff "\\ud800"}', b'{"params": \xff "R"}'),
        (b"[" * 200_000 + b"\\ud800", b"[" * 200_000 + b"R"),
    ],
)
def test_elsewhere_the_replacement_character_takes_its_place(
    line: bytes, found: bytes | None
) -> None:
    wanted = line.replace(b"\\ud800", b"R") if found is None else found
    assert parseable(line) == wanted.replace(b"R", b"\\" + b"ufffd")


@given(st.recursive(st.text(st.characters()) | st.integers(), lambda v: st.lists(v, max_size=4)))
def test_what_is_parseable_has_no_lone_surrogate_and_keeps_whole_text(value: object) -> None:
    line = message(value)

    found = parseable(line)

    _ = json.dumps(json.loads(found), ensure_ascii=False).encode()
    assert (found == line) is (refused_text(value) is None)
