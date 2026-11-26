"""Lone surrogates: where they are in arguments, and a message without them."""

import contextlib
import io
import json
import re
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from mcp.types import jsonrpc_message_adapter
from pydantic import ValidationError

from mcp_server_paperless.domain.texts import (
    REFUSED_METHOD,
    REFUSED_TEXT,
    parseable,
    refused_text,
)

type Json = dict[str, object]

BAD = "a\ud800b"


PLACES: list[tuple[object, str | None]] = [
    ({"title": BAD}, "title"),
    ({"title": "\udfff"}, "title"),
    ({"a": "\U0001f9fe", "b": 1, "c": None, "d": ["x", {"y": BAD}]}, "d.1.y"),
    ({"a": {"b": {BAD: 1}}}, "name of a value in a.b"),
    ({"a": [{BAD: 1}]}, "name of a value in a.0"),
    ({BAD: 1}, "name of an argument"),
    (BAD, "arguments"),
    ([1, BAD], "arguments.1"),
    ({"x" * 100: BAD}, "x" * 100),
    ({"x" * 300: BAD}, "x" * 99 + "…"),
    ({"y" * 300: {BAD: 1}}, "name of a value in " + "y" * 80 + "…"),
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
    line = message({"note": BAD, "other": "kept nowhere"}, _meta={"x": "\udc00", "y": "€"})

    found = parseable(line + b"\n")

    assert found.endswith(b"\n")
    assert json.loads(found) == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "add_note",
            "arguments": {REFUSED_TEXT: "note"},
            "_meta": {"x": "�", "y": "€"},
        },
    }


@pytest.mark.parametrize(
    ("params", "place"),
    [
        ({"name": BAD, "arguments": {}}, "name"),
        ({"name": "add_note", "arguments": {"note": "x"}, "_meta": {"t": BAD}}, "_meta.t"),
        ({"name": "add_note", "arguments": {"note": "x"}, BAD: 1}, "message"),
        ({"name": "add_note", BAD: BAD}, "message"),
        ({"name": "add_note", "arguments": BAD}, "arguments"),
        ({"name": "add_note"}, "message"),
    ],
)
def test_a_call_with_it_anywhere_is_refused_too(params: dict[str, object], place: str) -> None:
    """It would write, and it did not before."""
    body = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params, "extra": BAD}

    found = cast("dict[str, dict[str, object]]", json.loads(parseable(json.dumps(body).encode())))

    assert found["params"]["arguments"] == {REFUSED_TEXT: place}


@pytest.mark.parametrize("extra", [b', "error": {"code": 1, "message": "x"}}', b', "result": {}}'])
@pytest.mark.parametrize("method", ["tools/call", "x"])
def test_nothing_else_of_a_message_is_kept(method: str, extra: bytes) -> None:
    """An error or a result beside the method would make it an answer to what the server asked."""
    found = cast("Json", json.loads(parseable(message({"a": BAD}, method)[:-1] + extra)))

    assert set(found) == {"jsonrpc", "id", "method", "params"}


@pytest.mark.parametrize("method", ["prompts/get", "resources/read", "tools/call " + BAD])
def test_another_request_is_refused_by_a_method_that_says_why(method: str) -> None:
    found = parseable(message({"a": BAD}, method, _meta={"v": 1}))

    assert json.loads(found) == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": REFUSED_METHOD,
        "params": {"_meta": {"v": 1}},
    }


UNTOUCHED = [
    message({"note": "\U0001f9fe \ud83e\uddfe"}),
    message({"note": r"\ud800 \\ud800"}),
    '{"note": "\U0001f9fe €"}'.encode(),
    b'{"note": "\\\\ud800"}\n',
    b"\xff\xfe no json \\x",
    b"",
    b"\n",
    # What the SDK would not take without the escape either, and what has no answer.
    b"no json \\ud800\n",
    b'["\\ud800"]',
    b'{"id": "\\ud800"}',
    message({"note": BAD})[:-1],
    message({"note": BAD}) + b"x",
    message({"note": BAD}).replace(b'"note"', b"\xff"),
    message({"note": BAD}).replace(b'{"note"', b'\r{"note"'),
    message({"note": BAD}).replace(b"d800", b"d8zz"),
    message({"note": BAD}).replace(b"d800", b'd8\\"x\\"'),
    message({"note": BAD}).replace(b'"id": 7', b'"id": "\\ud800"'),
    message({"note": BAD}).replace(b'"id": 7', b'"id": null'),
    message({"note": BAD}).replace(b'"id": 7', b'"id": [1]'),
    message({"note": BAD}).replace(b'"id": 7', b'"id": true'),
    message({"note": BAD}).replace(b'"id": 7, ', b""),
    message({"note": BAD}).replace(b'"method"', b'"other"'),
    message({"note": BAD}).replace(b'"tools/call"', b"[]"),
    b'{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": ["\\ud800"]}',
    b'{"jsonrpc": "2.0", "id": 7, "result": {"a": "\\ud800"}}',
    b'{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "x": "\\ud800", "params": '
    + b'{"a":' * 500
    + b"1"
    + b"}" * 501,
    b"[" * 200_000 + b"\\ud800",
]


@pytest.mark.parametrize("line", UNTOUCHED)
def test_a_line_that_is_not_for_the_filter_stays_as_it_is(line: bytes) -> None:
    assert parseable(line) == line


def test_the_text_of_a_line_is_read_as_the_sdk_reads_it() -> None:
    """Bytes that are no UTF-8 become U+FFFD, in the place of the refusal too."""
    line = message({"note": BAD}, _meta={"x": "y"}).replace(b'"y"', b'"\xff"')

    found = cast("dict[str, dict[str, object]]", json.loads(parseable(line)))

    assert found["params"] == {
        "name": "add_note",
        "arguments": {REFUSED_TEXT: "note"},
        "_meta": {"x": "�"},
    }


def taken(data: bytes) -> list[Json]:
    """The messages the stdio transport of the SDK takes from ``data`` (mcp.server.stdio)."""
    text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace")
    found: list[Json] = []
    for line in text:
        with contextlib.suppress(ValidationError):
            message_ = jsonrpc_message_adapter.validate_json(line, by_name=False)
            found.append(message_.model_dump(by_alias=True, exclude_unset=True))
    return found


LONE = re.compile(
    rb"(?<!\\)((?:\\\\)*)\\u(?:d[89ab][0-9a-f]{2}(?!\\ud[c-f])|d[c-f][0-9a-f]{2})", re.IGNORECASE
)
PIECES = [
    *(b'"note"', b'"name"', b'"add_note"', b'"id"', b"7", b'"jsonrpc"', b'"2.0"', b'"method"'),
    *(b'"tools/call"', b'"prompts/get"', b'"params"', b'"arguments"', b"{", b"}", b"[", b"]"),
    *(
        b'"error"',
        b'"result"',
        b'{"code": 1, "message": "x"}',
        b', "error": {"code": 1, "message": "x"}',
    ),
    *(b', "result": {}', b', "result": {"action": "accept", "content": {}}'),
    *(b":", b",", b'"', b"\\", b"\\\\", b"\\u", b"\\ud800", b"\\uDFFF", b"\\ud83e\\uddfe", b"d8"),
    *(
        b"\xff",
        b"\xed\xa0\x80",
        b"\r",
        b"\n",
        b" ",
        b"null",
        b"NaN",
        b"1e999",
        b"\x00",
        b"\xef\xbb\xbf",
    ),
]
WHOLE = [
    message({"note": BAD}),
    message({"note": "fine", "b": [BAD, {BAD: 1}]}),
    message({"note": "fine"}, _meta={"x": BAD}),
    message({"a": BAD}, "prompts/get"),
    message({"note": "fine \U0001f9fe"}),
    message({"note": BAD})[:-1] + b', "error": {"code": 1, "message": "x"}}',
    message({"a": BAD}, "prompts/get")[:-1] + b', "result": {"action": "accept"}}',
    message({"note": BAD}) + b"\r\n" + message({"note": "second"}) + b"\n",
]


@st.composite
def changed(draw: st.DrawFn) -> bytes:
    """A message with pieces put in, taken out or written over anywhere."""
    line = draw(st.sampled_from(WHOLE))
    for _ in range(draw(st.integers(0, 4))):
        at = draw(st.integers(0, len(line)))
        until = min(len(line), at + draw(st.integers(0, 6)))
        line = line[:at] + draw(st.sampled_from([b"", *PIECES])) + line[until:]
    return line


@settings(max_examples=2000, deadline=None)
@given(changed() | st.lists(st.sampled_from(PIECES)).map(b"".join) | st.binary())
def test_a_message_the_sdk_rejects_is_never_run(data: bytes) -> None:
    """The filter changes the verdict on a message only from rejected to refused with an answer."""
    before = taken(data)
    filtered = b"".join(map(parseable, io.BytesIO(data)))

    after = taken(filtered)

    known = [repr(found) for found in before]  # not ==, which NaN never is
    refusals = [found for found in after if repr(found) not in known]
    assert [repr(found) for found in after if repr(found) in known] == known
    for found in refusals:
        assert LONE.search(data)
        assert set(found) == {"jsonrpc", "id", "method", "params"}  # no answer to anything
        assert found["id"] is not None
        params = cast("Json", found["params"])
        if found["method"] == "tools/call":
            assert list(cast("Json", params["arguments"])) == [REFUSED_TEXT]
        else:
            assert found["method"] == REFUSED_METHOD
            assert set(params) <= {"_meta"}


@pytest.mark.parametrize("text", ["\udc00\udc01", "\udbff\U0001f9fe\udfff", "\\\ud800"])
def test_surrogates_in_a_row_that_are_no_pair_are_lone(text: str) -> None:
    line = message({"a": text})

    assert len(taken(parseable(line))) == 1
    assert taken(line) == []


@pytest.mark.parametrize("escape", [b"\\uD800", b"\\uDfFf", b"\\udBFF"])
def test_the_digits_of_an_escape_can_be_capitals(escape: bytes) -> None:
    assert len(taken(parseable(message({"a": "x"}).replace(b'"x"', b'"' + escape + b'"')))) == 1


@given(st.recursive(st.text(st.characters()) | st.integers(), lambda v: st.lists(v, max_size=4)))
def test_every_call_with_a_lone_surrogate_is_answered(value: object) -> None:
    line = message({"a": value})

    found = parseable(line)

    assert len(taken(found)) == 1
    assert (found == line) is (refused_text(value) is None)
