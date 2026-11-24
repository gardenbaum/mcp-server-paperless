"""URLs without their credentials, in values and in text."""

import time

import pytest

from mcp_server_paperless.domain.urls import (
    URL_NOT_SHOWN,
    shown_url,
    text_without_credentials,
    without_empty_user,
)

pytestmark = pytest.mark.decision("decision-66")


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("https://h/x", "https://h/x"),
        ("https://u:p@h/x", "https://h/x"),
        ("https://u:p/q@h/x", URL_NOT_SHOWN),
        ("https:u:p@h/x", URL_NOT_SHOWN),
        ("https://u:p\uff20h", URL_NOT_SHOWN),
        ("https://u:p\ufe6bh", URL_NOT_SHOWN),
        ("https://u:p%40h/x", URL_NOT_SHOWN),
        ("https://u:p%40q@h/x?a=b%40c", "https://h/x?a=b%40c"),
    ],
)
def test_the_shown_form(url: str, shown: str) -> None:
    assert shown_url(url) == shown


@pytest.mark.parametrize(
    ("url", "written"),
    [
        ("https://@h/x", "https://h/x"),
        ("https://:@h", "https://h"),
        ("https://@h/x@y?z@", "https://h/x@y?z@"),
        ("https://@u@h/x", "https://@u@h/x"),
        ("https://u@h/x", "https://u@h/x"),
    ],
)
def test_an_empty_user_is_taken_off(url: str, written: str) -> None:
    assert without_empty_user(url) == written


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("no url, a@b.example", "no url, a@b.example"),
        ("a@b, see https://h/x ok", "a@b, see https://h/x ok"),
        (
            "to 'https://u:p@h/x' ok",
            "to 'https://h/x' ok",
        ),
        (
            "a@b at (1)https://u:p@h/x: why",
            "a@b at (1)https://h/x: why",
        ),
        (
            "to HTTPS://u:p@h",
            "to HTTPS://h",
        ),
        (
            "at https://u:p%40q@h/x",
            "at https://h/x",
        ),
        (
            "a https://u:p@h/x  http://v:w@i b",
            "a https://h/x  http://i b",
        ),
        (
            "see https://h/x?to=a%40b ok",
            "see https://h/x?to=a%40b ok",
        ),
        (
            "to https://u:p/q@h/x ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to '://u:p@h/x ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to https:u:p@h ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to https:/u:p@h ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to HTTPS:\\\\u:p@h ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to HTTP:u:p w@h ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "URI u:p@h, missing",
            f"URI {URL_NOT_SHOWN} missing",
        ),
        (
            "to https://u:p%40h/x ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to https://u:p\uff20h ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to https://u:p\ufe6bh ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to https://u:p w@h/x",
            f"to {URL_NOT_SHOWN}",
        ),
        (
            "to 'https://u:p'\"w@h/x' ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to https://u:p@q w@h/x: no",
            f"to {URL_NOT_SHOWN} no",
        ),
        (
            "to https://u:p ://q@h no",
            f"to {URL_NOT_SHOWN} no",
        ),
        (
            "a https://u:p://q@h b",
            f"a {URL_NOT_SHOWN} b",
        ),
        (
            "a https://u:p://q r@h b",
            f"a {URL_NOT_SHOWN} b",
        ),
        (
            "a x@y://https://h b",
            f"a {URL_NOT_SHOWN} b",
        ),
        (
            "a https://h for https://u:p@h b",
            f"a {URL_NOT_SHOWN} b",
        ),
        (
            "a https://h/x: mail to a@b c",
            f"a {URL_NOT_SHOWN} c",
        ),
        (
            "a https://h/x?m=a@b c",
            f"a {URL_NOT_SHOWN} c",
        ),
    ],
)
def test_credentials_in_text_go_and_more_only_where_it_cannot_be_told(
    text: str, shown: str
) -> None:
    assert text_without_credentials(text) == shown


@pytest.mark.parametrize(
    "part", ["://", "a", "@", "://@", "a:", "h://u@ ", "https: ", "https://u@h "]
)
def test_a_hostile_text_is_read_in_linear_time(part: str) -> None:
    started = time.process_time()

    _ = text_without_credentials(part * 300_000)

    assert time.process_time() - started < 5  # quadratic would take minutes
