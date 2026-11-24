"""RawApiService: what paperless_api_get may ask for and what it may hand back (decision-25).

The path rules are the security core of the raw toolset, so every rejected shape is a row of a
table, and a property test shows that no path the check accepts can leave ``/api/``.
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urljoin, urlsplit

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.errors import PaperlessMcpError, Validation
from mcp_server_paperless.domain.raw import RawReply
from mcp_server_paperless.services.raw import (
    MAX_OUTPUT,
    MAX_PARAMS,
    MAX_PATH,
    MAX_SEGMENTS,
    READ_LIMIT,
    REDACTED,
    RawApiService,
    check_params,
    check_path,
    redact,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-25")]

JSON = "application/json"


class Recording:
    """A RawReader that answers with `reply` and records what it was asked."""

    def __init__(self, reply: RawReply | None = None) -> None:
        self.reply: RawReply = reply or json_reply({"ok": True})
        self.calls: list[tuple[str, Mapping[str, str], int]] = []

    async def get(self, path: str, params: Mapping[str, str], *, max_bytes: int) -> RawReply:
        self.calls.append((path, dict(params), max_bytes))
        return self.reply


def json_reply(value: object, *, status: int = 200, content_type: str = JSON) -> RawReply:
    return RawReply(
        status=status, content_type=content_type, body=json.dumps(value).encode(), truncated=False
    )


# --- Paths that must never reach Paperless ------------------------------------------------------

REJECTED = {
    # /api/ alone is the schema view that redirects.
    "api root": "/api/",
    # Not under /api/.
    "empty": "",
    "root": "/",
    "api without slash": "/api",
    "relative": "api/tags/",
    "other prefix": "/admin/",
    "static": "/static/x.js",
    "api lookalike": "/apis/tags/",
    "api glued": "/apitags/",
    "uppercase api": "/API/tags/",
    "prefix only after a segment": "/x/api/tags/",
    # Absolute and scheme-relative URLs, a changed host or scheme.
    "absolute url": "http://evil.test/api/tags/",
    "absolute https": "https://paperless.test/api/tags/",
    "absolute with userinfo": "http://paperless.test@evil.test/api/tags/",
    "scheme relative": "//evil.test/api/tags/",
    "scheme relative api": "//api/tags/",
    "triple slash": "///api/tags/",
    "file scheme": "file:///etc/passwd",
    "gopher": "gopher://evil.test/",
    "javascript": "javascript:alert(1)",
    "data url": "data:text/plain,hi",
    "host after api": "/api@evil.test/",
    "at sign": "/api/tags/@evil.test",
    "at sign first": "@evil.test/api/tags/",
    "colon port": "/api:80/tags/",
    "colon in segment": "/api/tags:1/",
    "ipv6 literal": "/api/[::1]/",
    # Backslashes (browsers and some servers read them as slashes).
    "backslash": "/api\\tags/",
    "backslash host": "\\\\evil.test\\api\\tags\\",
    "backslash traversal": "/api/..\\admin/",
    "encoded backslash": "/api/%5Ctags/",
    # Traversal, plain and encoded.
    "dot dot": "/api/../admin/",
    "dot dot at end": "/api/tags/..",
    "dot dot inside": "/api/tags/../profile/",
    "dot segment": "/api/./tags/",
    "dot at end": "/api/tags/.",
    "encoded dots": "/api/%2e%2e/admin/",
    "encoded dots upper": "/api/%2E%2E/admin/",
    "encoded dot slash": "/api/%2e%2e%2fadmin/",
    "mixed dot": "/api/.%2e/admin/",
    "mixed dot 2": "/api/%2e./admin/",
    "double encoded dots": "/api/%252e%252e/admin/",
    "double encoded slash": "/api/tags%252f..%252fadmin/",
    "triple encoded": "/api/%25252e%25252e/",
    "encoded slash": "/api/tags%2f..%2fprofile/",
    "encoded slash upper": "/api/tags%2F..%2Fprofile/",
    "encoded percent": "/api/%25/",
    "bare percent": "/api/tags%/",
    "short percent": "/api/tags%2/",
    "encoded letter": "/api/%74ags/",
    "encoded api": "/%61pi/tags/",
    "encoded leading slash": "%2fapi/tags/",
    "encoded scheme relative": "%2f%2fevil.test/api/",
    "overlong utf8 dot": "/api/%c0%ae%c0%ae/admin/",
    "overlong utf8 slash": "/api/..%c0%af/",
    "unicode dots": "/api/\u2025/admin/",
    "fullwidth dots": "/api/\uff0e\uff0e/admin/",
    "fullwidth slash": "/api\uff0ftags/",
    "unicode letters": "/api/t\u00e4gs/",
    "unicode slash": "/api/\u2215tags/",
    "unicode backslash": "/api/\u29f5tags/",
    "unicode at": "/api/tags/\uff20evil",
    "non-breaking space": "/api/tags\u00a0/",
    "zero-width": "/api/ta\u200bgs/",
    "right to left": "/api/\u202etags/",
    # Empty segments.
    "double slash": "/api//tags/",
    "double slash at end": "/api/tags//",
    "double slash after id": "/api/documents/1//",
    # Query strings, fragments and parameters smuggled into the path.
    "query": "/api/tags/?name=a",
    "bare question mark": "/api/tags/?",
    "query in segment": "/api/tags?x=1/",
    "encoded question mark": "/api/tags%3fx=1/",
    "encoded question mark upper": "/api/tags%3Fx=1/",
    "fragment": "/api/tags/#x",
    "encoded fragment": "/api/tags%23x/",
    "path parameter": "/api/tags;x=1/",
    "encoded semicolon": "/api/tags%3bx=1/",
    "ampersand": "/api/tags&x=1/",
    "equals": "/api/tags=1/",
    # Whitespace, control characters and new lines.
    "space": "/api/ tags/",
    "trailing space": "/api/tags/ ",
    "leading space": " /api/tags/",
    "tab": "/api/\ttags/",
    "newline": "/api/tags/\n",
    "newline inside": "/api/ta\ngs/",
    "carriage return": "/api/tags/\r",
    "crlf injection": "/api/tags/\r\nHost: evil.test",
    "encoded crlf": "/api/tags/%0d%0aHost:evil.test",
    "encoded newline": "/api/tags/%0a",
    "null byte": "/api/tags/\x00",
    "encoded null": "/api/tags/%00",
    "escape": "/api/tags/\x1b",
    "delete": "/api/tags/\x7f",
    "vertical tab": "/api/tags/\x0b",
    "next line": "/api/tags/\x85",
    "line separator": "/api/tags/\u2028",
    # Other characters that mean something to a URL or a shell.
    "quote": '/api/tags/"',
    "apostrophe": "/api/tags/'",
    "angle brackets": "/api/<tags>/",
    "pipe": "/api/tags|x/",
    "caret": "/api/tags^/",
    "backtick": "/api/`tags`/",
    "braces": "/api/{id}/",
    "brackets": "/api/tags[0]/",
    "plus": "/api/tags+x/",
    "comma": "/api/tags,x/",
    "star": "/api/*/",
    "dollar": "/api/$x/",
    "bang": "/api/tags!/",
    "tilde": "/api/~root/",
    "dot in name": "/api/tags.json",
    "dotfile": "/api/.env",
    "too long": "/api/" + "a" * 300 + "/",
    "too many segments": "/api/" + "a/" * 20,
    "non-string like": "/api/\ud800/",
}

# Paths of the vendored API that must go through.
ACCEPTED = (
    "/api/tags/",
    "/api/tags",
    "/api/tags/12/",
    "/api/documents/",
    "/api/documents/7/history/",
    "/api/documents/7/metadata/",
    "/api/documents/next_asn/",
    "/api/saved_views/",
    "/api/custom_fields/3/",
    "/api/tasks/active/",
    "/api/statistics/",
    "/api/status/",
    "/api/search/autocomplete/",
    "/api/share_links/",
    "/api/workflow-triggers/",
    "/api/A-Z_0-9/",
)


@pytest.mark.parametrize("path", REJECTED.values(), ids=list(REJECTED))
async def test_a_hostile_path_is_refused_before_anything_is_sent(path: str) -> None:
    reader = Recording()

    with pytest.raises(Validation):
        _ = await RawApiService(reader).get(path, {})

    assert reader.calls == []


@pytest.mark.parametrize("path", REJECTED.values(), ids=list(REJECTED))
def test_the_path_check_refuses_every_hostile_path(path: str) -> None:
    with pytest.raises(Validation):
        _ = check_path(path)


@pytest.mark.parametrize("path", ACCEPTED)
async def test_a_plain_api_path_is_sent_as_it_is(path: str) -> None:
    reader = Recording()

    result = await RawApiService(reader).get(path, {})

    assert reader.calls == [(path, {}, READ_LIMIT)]
    assert result.path == path


def test_a_refusal_says_what_a_path_may_look_like() -> None:
    with pytest.raises(Validation) as refused:
        _ = check_path("https://evil.test/")

    assert "/api/" in refused.value.message
    assert refused.value.hint is not None
    assert "/api/tags/" in refused.value.hint


def test_a_refusal_never_repeats_the_hostile_text() -> None:
    with pytest.raises(Validation) as refused:
        _ = check_path("/api/tags/\r\nHost: evil.test\x00")

    assert "evil" not in refused.value.describe()
    assert "\r" not in refused.value.describe()


# --- Whatever the check accepts stays on the API ------------------------------------------------

FRAGMENTS = st.sampled_from(
    [
        *("..", ".", "%2e", "%2E", "%2e%2e", "%252e", "%2f", "%5c", "%25", "%00", "%0a"),
        *("/", "//", "\\", "@", "?", "#", ":", ";", "&", "=", " ", "\t", "\n", "\x00"),
        *("api", "tags", "1", "a-b", "_", "\u2025", "\uff0e", "\u2215", "\u00e4", "%"),
        *("http:", "https://", "evil.test", "[", "]", "{", "}", "|", "^", "`", "~"),
    ]
)


def _under_api(tail: str) -> str:
    return "/api/" + tail


CANDIDATES: st.SearchStrategy[str] = st.one_of(
    st.text(max_size=40),
    st.builds("".join, st.lists(FRAGMENTS, max_size=10)),
    st.builds(_under_api, st.builds("".join, st.lists(FRAGMENTS, max_size=8))),
    st.builds(_under_api, st.text(max_size=30)),
)


@given(CANDIDATES)
def test_an_accepted_path_cannot_leave_the_api(candidate: str) -> None:
    try:
        path = check_path(candidate)
    except Validation:
        return

    parts = urlsplit(urljoin("http://paperless.test", path))
    assert (parts.scheme, parts.netloc, parts.query, parts.fragment) == (
        "http",
        "paperless.test",
        "",
        "",
    )
    assert parts.path == path
    assert path.startswith("/api/")
    segments = path.removeprefix("/").removesuffix("/").split("/")
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", segment) for segment in segments)
    assert not {
        "profile",
        "config",
        "mail_accounts",
        "token",
        "oauth",
        "download",
        "preview",
        "thumb",
        "bulk_download",
    } & {segment.lower() for segment in segments}


# --- Endpoints that hold secrets or files -------------------------------------------------------

DENIED = {
    "profile": "/api/profile/",
    "profile no slash": "/api/profile",
    "profile totp": "/api/profile/totp/",
    "profile providers": "/api/profile/social_account_providers/",
    "profile upper": "/api/PROFILE/",
    "profile mixed": "/api/Profile/",
    "config": "/api/config/",
    "config one": "/api/config/1/",
    "mail accounts": "/api/mail_accounts/",
    "mail account": "/api/mail_accounts/3/",
    "token": "/api/token/",
    "token upper": "/api/Token/",
    "oauth": "/api/oauth/callback/",
    "oauth upper": "/api/OAUTH/callback/",
    "download": "/api/documents/1/download/",
    "download upper": "/api/documents/1/DOWNLOAD/",
    "preview": "/api/documents/1/preview/",
    "thumb": "/api/documents/1/thumb/",
    "bulk download": "/api/documents/bulk_download/",
    "bundle download": "/api/share_link_bundles/2/download/",
    "deep profile": "/api/documents/1/profile/",
    "download no slash": "/api/documents/1/download",
    "ai suggestions": "/api/documents/1/ai_suggestions/",
    "ai suggestions upper": "/api/documents/1/AI_Suggestions",
}


@pytest.mark.parametrize("path", DENIED.values(), ids=list(DENIED))
async def test_an_endpoint_with_secrets_or_files_is_denied_without_a_request(path: str) -> None:
    reader = Recording()

    with pytest.raises(Validation) as refused:
        _ = await RawApiService(reader).get(path, {})

    assert reader.calls == []
    assert refused.value.hint is not None


def test_the_hint_for_a_denied_endpoint_names_the_curated_tool() -> None:
    with pytest.raises(Validation) as refused:
        _ = check_path("/api/documents/1/download/")
    assert "download_document" in refused.value.describe()
    with pytest.raises(Validation) as refused:
        _ = check_path("/api/mail_accounts/")
    assert "list_mail_accounts" in refused.value.describe()


# --- Query parameters ---------------------------------------------------------------------------


def test_params_become_text_without_changing_their_meaning() -> None:
    assert check_params({"page_size": 5, "ordering": "-created", "flag": True, "off": False}) == {
        "page_size": "5",
        "ordering": "-created",
        "flag": "true",
        "off": "false",
    }
    assert check_params(None) == {}


def test_values_may_hold_any_text_without_control_characters() -> None:
    value = "tags__id__all=1&x=\u00fc/../?#%2e"
    assert check_params({"query": value}) == {"query": value}


BAD_PARAMS: dict[str, Mapping[str, str | int | bool]] = {
    "empty name": {"": "x"},
    "space in name": {"a b": "x"},
    "newline in name": {"a\nb": "x"},
    "equals in name": {"a=b": "x"},
    "ampersand in name": {"a&b": "x"},
    "percent in name": {"a%20b": "x"},
    "unicode name": {"\u00e4": "x"},
    "long name": {"a" * 65: "x"},
    "newline in value": {"a": "x\ny"},
    "carriage return in value": {"a": "x\ry"},
    "null in value": {"a": "x\x00"},
    "tab in value": {"a": "x\ty"},
    "escape in value": {"a": "\x1b[0m"},
    "delete in value": {"a": "\x7f"},
    "line separator in value": {"a": "\u2028"},
    "format": {"format": "json"},
    "format upper": {"Format": "json"},
    "long value": {"a": "x" * 2001},
    "too many": {f"p{n}": "x" for n in range(MAX_PARAMS + 1)},
}


@pytest.mark.parametrize("params", BAD_PARAMS.values(), ids=list(BAD_PARAMS))
async def test_bad_params_are_refused_before_anything_is_sent(
    params: Mapping[str, str | int | bool],
) -> None:
    reader = Recording()

    with pytest.raises(Validation):
        _ = await RawApiService(reader).get("/api/tags/", params)

    assert reader.calls == []


async def test_params_reach_the_reader_as_text() -> None:
    reader = Recording()

    _ = await RawApiService(reader).get("/api/tags/", {"page_size": 2, "name__icontains": "a"})

    assert reader.calls == [("/api/tags/", {"page_size": "2", "name__icontains": "a"}, READ_LIMIT)]


# --- What comes back ----------------------------------------------------------------------------


async def test_a_json_answer_is_returned_parsed() -> None:
    reader = Recording(json_reply({"count": 1, "results": [{"id": 1, "name": "a"}]}))

    result = await RawApiService(reader).get("/api/tags/", {})

    assert result.status == 200
    assert result.data == {"count": 1, "results": [{"id": 1, "name": "a"}]}
    assert result.truncated is False
    assert result.text is None
    assert result.redacted == 0
    assert result.note is None


@pytest.mark.parametrize(
    "content_type", [JSON, "application/json; charset=utf-8", "Application/JSON"]
)
async def test_json_content_types_are_accepted(content_type: str) -> None:
    reader = Recording(json_reply([1], content_type=content_type))

    assert (await RawApiService(reader).get("/api/tags/", {})).data == [1]


@pytest.mark.parametrize(
    "content_type",
    ["application/pdf", "text/html", "image/webp", "text/plain", "", "application/jsonx"],
)
async def test_an_answer_that_is_not_json_is_refused_without_its_body(content_type: str) -> None:
    reply = RawReply(status=200, content_type=content_type, body=b"SECRET-BYTES", truncated=False)

    with pytest.raises(PaperlessMcpError) as refused:
        _ = await RawApiService(Recording(reply)).get("/api/tags/", {})

    assert "SECRET-BYTES" not in refused.value.describe()
    assert "JSON" in refused.value.message


async def test_an_empty_answer_has_no_data() -> None:
    reply = RawReply(status=204, content_type="", body=b"", truncated=False)

    result = await RawApiService(Recording(reply)).get("/api/tags/", {})

    assert (result.status, result.data, result.truncated, result.text) == (204, None, False, None)


async def test_json_that_does_not_parse_is_an_unexpected_answer() -> None:
    reply = RawReply(status=200, content_type=JSON, body=b"{not json", truncated=False)

    with pytest.raises(PaperlessMcpError, match="unexpected"):
        _ = await RawApiService(Recording(reply)).get("/api/tags/", {})


async def test_an_answer_over_the_output_cap_is_cut_and_marked() -> None:
    rows = [{"id": n, "name": f"tag-{n:05d}"} for n in range(2000)]
    reader = Recording(json_reply({"results": rows}))

    result = await RawApiService(reader).get("/api/tags/", {})

    assert result.truncated is True
    assert result.data is None
    assert result.text is not None
    assert len(result.text) == MAX_OUTPUT
    assert result.text.startswith('{"results":[{"id":0,"name":"tag-00000"}')
    assert result.note is not None
    assert "page_size" in result.note


async def test_an_answer_just_under_the_cap_is_not_cut() -> None:
    padding = MAX_OUTPUT - len('{"a":""}')
    reader = Recording(json_reply({"a": "x" * padding}))

    result = await RawApiService(reader).get("/api/tags/", {})

    assert result.truncated is False
    assert result.data == {"a": "x" * padding}


async def test_an_answer_one_character_over_the_cap_is_cut() -> None:
    padding = MAX_OUTPUT - len('{"a":""}') + 1
    reader = Recording(json_reply({"a": "x" * padding}))

    result = await RawApiService(reader).get("/api/tags/", {})

    assert result.truncated is True
    assert result.text is not None
    assert len(result.text) == MAX_OUTPUT


async def test_an_answer_too_large_to_read_has_no_text_at_all() -> None:
    reply = RawReply(status=200, content_type=JSON, body=b"", truncated=True)

    result = await RawApiService(Recording(reply)).get("/api/documents/", {})

    assert result.truncated is True
    assert result.data is None
    assert result.text is None
    assert result.note is not None
    assert "page_size" in result.note


async def test_the_cut_text_is_redacted_like_the_data() -> None:
    rows = [{"id": n, "password": "hunter2", "pad": "x" * 100} for n in range(500)]
    reader = Recording(json_reply(rows))

    result = await RawApiService(reader).get("/api/workflow_actions/", {})

    assert result.truncated is True
    assert result.text is not None
    assert "hunter2" not in result.text
    assert REDACTED in result.text


# --- Redaction ----------------------------------------------------------------------------------


def test_secret_looking_keys_are_replaced_at_any_depth() -> None:
    value = cast(
        "JsonValue",
        {
            "id": 1,
            "password": "p",
            "passwords": ["a", "b"],
            "llm_api_key": "k",
            "remote_ocr_api_key": None,
            "auth_token": "t",
            "client_secret": "s",
            "webhook": {"url": "http://x", "headers": {"Authorization": "Bearer z"}},
            "results": [{"name": "n", "Password": "q", "nested": [{"deep_password": "d"}]}],
        },
    )

    cleaned, count = redact(value)

    assert cleaned == {
        "id": 1,
        "password": REDACTED,
        "passwords": REDACTED,
        "llm_api_key": REDACTED,
        "remote_ocr_api_key": None,
        "auth_token": REDACTED,
        "client_secret": REDACTED,
        "webhook": {"url": "http://x", "headers": REDACTED},
        "results": [{"name": "n", "Password": REDACTED, "nested": [{"deep_password": REDACTED}]}],
    }
    assert count == 8


def test_names_that_only_look_alike_are_kept() -> None:
    value = cast("JsonValue", {"is_token": True, "token_expires": None, "tags": [1], "key": "k"})

    assert redact(value) == (value, 0)


def test_redaction_leaves_scalars_and_lists_alone() -> None:
    assert redact(5) == (5, 0)
    assert redact(None) == (None, 0)
    assert redact([1, "a", [True]]) == ([1, "a", [True]], 0)


async def test_a_redacted_answer_says_how_many_values_were_replaced() -> None:
    reader = Recording(json_reply({"results": [{"id": 1, "passwords": "x"}]}))

    result = await RawApiService(reader).get("/api/workflow_actions/", {})

    assert result.data == {"results": [{"id": 1, "passwords": REDACTED}]}
    assert result.redacted == 1
    assert result.note is not None
    assert "redacted" in result.note


# --- The vendored schema: no readable endpoint hands out a secret unguarded ---------------------

SCHEMA = Path(__file__).parents[2] / "docs" / "vendor" / "paperless" / "openapi.json"
# Response properties that sound like secrets but are flags.
FLAGS = frozenset({"is_token", "has_usable_password", "token_expires"})
SECRET_LOOKING = re.compile(
    r"password|passwd|secret|api_?key|token|private_key|^(?:headers|params|body)$", re.IGNORECASE
)


def response_properties(node: object, schemas: Mapping[str, object], seen: set[str]) -> set[str]:
    """Every property name reachable from a response schema, references followed."""
    names: set[str] = set()
    if isinstance(node, dict):
        fields = cast("dict[str, object]", node)
        reference = fields.get("$ref")
        if isinstance(reference, str):
            name = reference.rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                names |= response_properties(schemas[name], schemas, seen)
        for key, value in fields.items():
            if key == "properties" and isinstance(value, dict):
                names |= set(cast("dict[str, object]", value))
            names |= response_properties(cast("object", value), schemas, seen)
    elif isinstance(node, list):
        for item in cast("list[object]", node):
            names |= response_properties(item, schemas, seen)
    return names


def unguarded_secrets() -> dict[str, set[str]]:
    """GET endpoints of the schema with secret-looking properties that nothing guards."""
    schema = cast("dict[str, dict[str, object]]", json.loads(SCHEMA.read_text(encoding="utf-8")))
    schemas = cast("dict[str, object]", schema["components"]["schemas"])
    found: dict[str, set[str]] = {}
    for template, operations in cast("dict[str, dict[str, object]]", schema["paths"]).items():
        if "get" not in operations:
            continue
        concrete = re.sub(r"\{\w+\}", "1", template)
        try:
            _ = check_path(concrete)
        except Validation:
            continue
        names = response_properties(operations["get"], schemas, set())
        loose = {
            name
            for name in names
            if SECRET_LOOKING.search(name) and name not in FLAGS and not _redacted_name(name)
        }
        if loose:
            found[template] = loose
    return found


def _redacted_name(name: str) -> bool:
    return redact({name: "x"})[1] == 1


def test_every_readable_endpoint_of_the_schema_has_its_secret_fields_redacted() -> None:
    assert unguarded_secrets() == {}


def test_the_schema_check_finds_a_secret_that_nothing_guards() -> None:
    assert _redacted_name("llm_api_key")
    assert not _redacted_name("signing_key")


# --- Exact limits and wording (what a caller reads and relies on) -------------------------------

NARROWING = (
    "The answer is too long to return whole. Narrow it with params: page_size, page, "
    "fields, ordering or filters of the endpoint."
)


def test_a_path_may_be_exactly_as_long_as_the_limit_but_not_longer() -> None:
    longest = "/api/" + "a" * (MAX_PATH - len("/api/"))

    assert check_path(longest) == longest
    with pytest.raises(Validation) as refused:
        _ = check_path(longest + "a")
    assert refused.value.message == f"The path is refused: it is longer than {MAX_PATH} characters."


def test_a_path_may_have_as_many_segments_as_the_limit_but_not_more() -> None:
    most = "/api/" + "/".join("a" * MAX_SEGMENTS)

    assert check_path(most) == most
    with pytest.raises(Validation) as refused:
        _ = check_path(most + "/a")
    assert refused.value.message == (
        f"The path is refused: it has more than {MAX_SEGMENTS} segments."
    )


def test_the_refusal_of_a_plain_looking_path_has_exact_words() -> None:
    with pytest.raises(Validation) as refused:
        _ = check_path("/api/tags/?x=1")

    assert refused.value.message == (
        "The path is refused: only /api/ followed by names of letters, digits, _ and - is "
        "allowed, with no query, no encoded characters and no dot segments."
    )
    assert refused.value.hint == (
        "Use a relative path under /api/ such as /api/tags/ or /api/documents/12/history/; "
        "put filters in params, not in the path."
    )


def test_a_denied_endpoint_has_exact_words() -> None:
    with pytest.raises(Validation) as refused:
        _ = check_path("/api/Profile/")

    assert refused.value.message == "Reading /api/.../profile/ is not available: it holds secrets."
    assert refused.value.hint is not None
    assert refused.value.hint.startswith("The profile holds the API token")
    with pytest.raises(Validation) as refused:
        _ = check_path("/api/documents/1/thumb/")
    assert refused.value.message == "Reading /api/.../thumb/ is not available: it returns a file."
    assert refused.value.hint == (
        "Files are returned by download_document, get_document_pages and get_document_thumbnail, "
        "which cap their size."
    )


def test_as_many_params_as_the_limit_go_through_and_one_more_does_not() -> None:
    most = {f"p{n}": "x" for n in range(MAX_PARAMS)}

    assert check_params(most) == most
    with pytest.raises(Validation) as refused:
        _ = check_params({**most, "extra": "x"})
    assert refused.value.message == f"There are more than {MAX_PARAMS} params."
    assert refused.value.hint == "Send fewer filters."


def test_a_param_value_may_be_exactly_as_long_as_the_limit() -> None:
    value = "x" * 2000

    assert check_params({"a": value}) == {"a": value}
    with pytest.raises(Validation) as refused:
        _ = check_params({"a": value + "x"})
    assert refused.value.message == (
        "The value of param a is refused: at most 2000 characters and no control characters "
        "or new lines."
    )
    assert refused.value.hint == "Shorten the value."


def test_a_param_name_may_be_exactly_as_long_as_the_limit() -> None:
    name = "n" * 64

    assert check_params({name: 1}) == {name: "1"}
    with pytest.raises(Validation) as refused:
        _ = check_params({name + "n": 1})
    assert refused.value.message == (
        "A param name is refused: names have 1 to 64 letters, digits, _, - or ."
    )
    assert (
        refused.value.hint == "Use the names of the Paperless API, such as page_size or ordering."
    )


async def test_a_refused_content_type_has_the_hint_to_the_curated_tools() -> None:
    reply = RawReply(status=200, content_type="image/png", body=b"x", truncated=False)

    with pytest.raises(PaperlessMcpError) as refused:
        _ = await RawApiService(Recording(reply)).get("/api/tags/", {})

    assert refused.value.message == (
        "Paperless answered /api/tags/ with something other than JSON; it is not returned."
    )
    assert refused.value.hint is not None
    assert "download_document" in refused.value.hint


async def test_non_ascii_text_counts_as_characters_not_escapes() -> None:
    reader = Recording(
        RawReply(
            status=200,
            content_type=JSON,
            body=json.dumps({"a": "ü" * 10_000}, ensure_ascii=False).encode(),
            truncated=False,
        )
    )

    result = await RawApiService(reader).get("/api/tags/", {})

    assert result.truncated is False
    assert result.data == {"a": "ü" * 10_000}


async def test_an_unreadable_answer_reports_no_redactions_and_says_how_to_narrow() -> None:
    reply = RawReply(status=200, content_type=JSON, body=b"", truncated=True)

    result = await RawApiService(Recording(reply)).get("/api/documents/", {})

    assert (result.status, result.redacted, result.note) == (200, 0, NARROWING)


async def test_an_empty_answer_reports_no_redactions_and_no_note() -> None:
    reply = RawReply(status=204, content_type="", body=b"", truncated=False)

    result = await RawApiService(Recording(reply)).get("/api/tags/", {})

    assert (result.redacted, result.note) == (0, None)


async def test_a_cut_answer_without_secrets_has_only_the_narrowing_note() -> None:
    rows = [{"id": n, "pad": "x" * 100} for n in range(500)]

    result = await RawApiService(Recording(json_reply(rows))).get("/api/tags/", {})

    assert (result.redacted, result.note) == (0, NARROWING)


async def test_a_cut_answer_with_secrets_counts_them_and_says_so_after_the_narrowing() -> None:
    rows = [{"id": n, "password": "hunter2", "pad": "x" * 100} for n in range(500)]

    result = await RawApiService(Recording(json_reply(rows))).get("/api/workflow_actions/", {})

    assert result.redacted == 500
    assert result.note == f"{NARROWING} 500 secret value(s) were replaced with {REDACTED}."


async def test_an_answer_that_fits_says_how_many_secrets_were_replaced() -> None:
    reader = Recording(json_reply({"password": "a", "nested": {"api_key": "b"}}))

    result = await RawApiService(reader).get("/api/workflows/", {})

    assert result.note == f"2 secret value(s) were replaced with {REDACTED}."


# --- Review findings: host leak, hidden AI calls, more secret shapes -----------------------------


async def test_page_links_become_page_numbers_so_no_host_leaks() -> None:
    body = {
        "count": 60,
        "next": "http://127.0.0.1:8010/api/tasks/?page=3&page_size=25",
        "previous": "http://127.0.0.1:8010/api/tasks/",
        "results": [{"id": 1, "next": None, "url": "http://127.0.0.1:8010/api/x/"}],
    }

    result = await RawApiService(Recording(json_reply(body))).get("/api/tasks/", {})

    assert result.data == {
        "count": 60,
        "next": 3,
        "previous": 1,
        "results": [{"id": 1, "next": None, "url": "http://127.0.0.1:8010/api/x/"}],
    }
    assert "127.0.0.1" not in str(result.data).replace("http://127.0.0.1:8010/api/x/", "")
    assert result.redacted == 0


async def test_a_page_link_that_is_not_a_page_is_dropped() -> None:
    body = {"next": "http://h/api/tasks/?page=abc", "previous": "http://h/api/tasks/?page=2"}

    result = await RawApiService(Recording(json_reply(body))).get("/api/tasks/", {})

    assert result.data == {"next": None, "previous": 2}


def test_the_hint_for_ai_suggestions_names_the_curated_tool() -> None:
    with pytest.raises(Validation) as refused:
        _ = check_path("/api/documents/1/ai_suggestions/")

    assert refused.value.message == (
        "Reading /api/.../ai_suggestions/ is not available: it asks a language model."
    )
    assert refused.value.hint is not None
    assert "get_ai_suggestions" in refused.value.hint


def test_more_secret_shapes_are_replaced() -> None:
    value = cast(
        "JsonValue",
        {
            "webhook": {"url": "http://x", "params": {"token": "t"}, "body": "{{ secret }}"},
            "access_token": "a",
            "refresh_token": "r",
            "token": "t",
            "apiKey": "k",
            "api_key": "k",
            "private_key": "p",
            "has_usable_password": True,
            "is_token": True,
            "token_expires": "2027-01-01",
            "filter_body": "kept",
            "params": None,
        },
    )

    cleaned, count = redact(value)

    assert cleaned == {
        "webhook": {"url": "http://x", "params": REDACTED, "body": REDACTED},
        "access_token": REDACTED,
        "refresh_token": REDACTED,
        "token": REDACTED,
        "apiKey": REDACTED,
        "api_key": REDACTED,
        "private_key": REDACTED,
        "has_usable_password": True,
        "is_token": True,
        "token_expires": "2027-01-01",
        "filter_body": "kept",
        "params": None,
    }
    assert count == 8


def test_the_format_param_is_refused_with_exact_words() -> None:
    with pytest.raises(Validation) as refused:
        _ = check_params({"format": "json"})

    assert refused.value.message == (
        "The param format is refused: Paperless answers it with a misleading error."
    )
    assert refused.value.hint == "Answers are JSON already; leave format out."


@pytest.mark.decision("decision-66")
@pytest.mark.parametrize("nested", [False, True])
async def test_the_urls_of_webhooks_lose_their_credentials_and_the_result_says_so(
    *, nested: bool
) -> None:
    action = {
        "type": 4,
        "webhook": {"id": 3, "url": "https://user:pw@h.example/x", "headers": {"A": "b"}},
    }
    plain = {"type": 4, "webhook": {"id": 4, "url": "https://h.example/y"}, "url": "a:b@c"}
    unclear = {"type": 4, "webhook": {"id": 5, "url": "https://us/er:pw@h.example/x"}}
    body: object = {"results": [action, plain, unclear]}
    if nested:
        body = {"workflows": [{"actions": [action, plain, unclear]}]}

    result = await RawApiService(Recording(json_reply(body))).get("/api/workflow_actions/", {})

    text = json.dumps(result.data)
    assert "pw" not in text
    assert '"https://h.example/x"' in text
    assert '"a URL that is not shown"' in text
    assert '"a:b@c"' in text
    assert result.redacted == 3
    assert result.note == (
        "1 secret value(s) were replaced with [redacted]. "
        "2 URL(s) or log line(s) are shown without credentials."
    )


@pytest.mark.decision("decision-66")
async def test_log_lines_lose_the_credentials_of_the_urls_in_them() -> None:
    lines = [
        "[2026-11-24 10:00:00,123] Webhook sent to https://user:pw@h.example/x",
        "[2026-11-24 10:00:00,124] mail a@b.example",
        "[2026-11-24 10:00:00,125] to https://user:p",
        3,
        "w@h.example/x",
    ]

    result = await RawApiService(Recording(json_reply(lines))).get("/api/LOGS/paperless/", {})

    assert result.data == [
        "[2026-11-24 10:00:00,123] Webhook sent to https://h.example/x",
        "[2026-11-24 10:00:00,124] mail a@b.example",
        "[2026-11-24 10:00:00,125] to a URL that is not shown",
        3,
        "a URL that is not shown",
    ]
    assert result.redacted == 3


@pytest.mark.decision("decision-66")
@pytest.mark.parametrize(
    ("body", "shown"),
    [
        ("to https://user:pw@h", "to https://h"),
        ({"webhook": "https://user:pw@h"}, {"webhook": "https://h"}),
        (
            {"webhook": [{"x": {"url": "https://user:pw@h"}}]},
            {"webhook": [{"x": {"url": "https://h"}}]},
        ),
        (
            {
                "result_data": {
                    "error_message": "to https://user:pw@h",
                    "n": [1, "at https://u:pw@i"],
                }
            },
            {"result_data": {"error_message": "to https://h", "n": [1, "at https://i"]}},
        ),
        ({"redis_error": "no https://user:pw@h"}, {"redis_error": "no https://h"}),
        ({"database": {"error": "https://user:pw@h"}}, {"database": {"error": "https://h"}}),
        ({"last_error": {"m": "https://user:pw@h"}}, {"last_error": {"m": "https://h"}}),
        ({"title": "https://user:pw@h", "n": None}, {"title": "https://user:pw@h", "n": None}),
    ],
)
async def test_urls_in_webhooks_and_errors_lose_their_credentials(
    body: JsonValue, shown: JsonValue
) -> None:
    path = "/api/logs/x/" if isinstance(body, str) else "/api/tasks/"

    result = await RawApiService(Recording(json_reply(body))).get(path, {})

    assert result.data == shown
    assert (result.redacted > 0) == (body != shown)


@pytest.mark.decision("decision-66")
async def test_a_cut_answer_has_no_credentials_either() -> None:
    url = "https://user:pw@h.example/x"
    reply = json_reply([{"webhook": {"url": url}, "rest": "x" * MAX_OUTPUT}])

    result = await RawApiService(Recording(reply)).get("/api/workflows/", {})

    assert result.truncated
    assert "pw" not in str(result.text)
    assert "shown without credentials" in str(result.note)
