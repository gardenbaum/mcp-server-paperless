"""URLs without their credentials, in values and in text."""

import time

import pytest

from mcp_server_paperless.domain.urls import (
    URL_NOT_SHOWN,
    lines_without_credentials,
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
        ("//p%40h/x", URL_NOT_SHOWN),
        ("r?u=https%3A%2F%2Fu:p%40h", URL_NOT_SHOWN),
        ("http:///u:p%40h", URL_NOT_SHOWN),
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
        ("https://@@h/x", "https://h/x"),
        ("https://:@:@h", "https://h"),
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
        (
            "to //p@h/x ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to \\\\p@h/x ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to p@h:443 ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to p@[::1]/x ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "//p@h then http://x/y a",
            f"{URL_NOT_SHOWN} then http://x/y a",
        ),
        (
            "to //mailto:p@h ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to xmailto:p@h ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        (
            "to MAILTO:u:p@h ok",
            f"to {URL_NOT_SHOWN} ok",
        ),
        *(
            (f"to {word} failed", f"to {URL_NOT_SHOWN} failed")
            for word in (
                "user:secret/https://rest@host/x",
                "//user:secret/https://rest@h",
                "//secret/https://rest@h",
                "user:secret://rest@host",
                "//secret://rest@h",
                "u:p,https://q@h",
                "u:p/https://q/https://r@h",
                'u:p"https://q@h',
                "u:p=https://q%40h",
                'u:"p/https://q@h',
                "u:p/ftp://q@h",
                "u:/https://q@h",
            )
        ),
        *(
            (f"to {before}https://u:p@h{after} ok", f"to {before}https://h{after} ok")
            for before, after in (
                ("url:", ""),
                ('{"url":"', '"}'),
                ("{'url':['", "']}"),
                ("<", ">"),
                ("(see:", ")"),
                ("url=", ""),
            )
        ),
        # Left: what an e-mail address, a path of a file or a name can be.
        ("<mailto:a@b.example> ok", "<mailto:a@b.example> ok"),
        ("from a@b.example: why", "from a@b.example: why"),
        ("me@b.example.INBOX, a@b.example/INBOX", "me@b.example.INBOX, a@b.example/INBOX"),
        ("/media/a@b/c.pdf line 3:1", "/media/a@b/c.pdf line 3:1"),
        ("a@b.example", "a@b.example"),
    ],
)
def test_credentials_in_text_go_and_more_only_where_it_cannot_be_told(
    text: str, shown: str
) -> None:
    assert text_without_credentials(text) == shown


@pytest.mark.parametrize(
    "part",
    ["://", "a", "@", "://@", "a:", "h://u@ ", "https: ", "https://u@h ", "u:p/https://", ':"'],
)
def test_a_hostile_text_is_read_in_linear_time(part: str) -> None:
    started = time.process_time()

    _ = text_without_credentials(part * 300_000)

    assert time.process_time() - started < 5  # quadratic would take minutes


@pytest.mark.parametrize(
    ("lines", "shown"),
    [
        (["to https://u:p", "q@h/x ok"], [f"to {URL_NOT_SHOWN}", f"{URL_NOT_SHOWN} ok"]),
        (
            ["a", "to https://u:p", "", "q", "q@h", "z"],
            ["a", f"to {URL_NOT_SHOWN}", URL_NOT_SHOWN, URL_NOT_SHOWN, URL_NOT_SHOWN, "z"],
        ),
        (
            ["to https://u:p\nq", "q@h\nok"],
            [f"to {URL_NOT_SHOWN}\n{URL_NOT_SHOWN}", f"{URL_NOT_SHOWN}\nok"],
        ),
        (
            ["[2026-11-24 10:00:00,123] [INFO] https://h/x", "[2026-11-24 10:00:01,123] by a@b"],
            ["[2026-11-24 10:00:00,123] [INFO] https://h/x", "[2026-11-24 10:00:01,123] by a@b"],
        ),
        ([], []),
    ],
)
def test_a_url_split_over_lines_of_a_record_loses_its_credentials(
    lines: list[str], shown: list[str]
) -> None:
    start = "[2026-11-24 09:00:00,123] [INFO] the first line of the record"

    assert lines_without_credentials([start, *lines]) == [start, *shown]


@pytest.mark.parametrize("line", ["https://u:p", "q@h", "[2026-11-24 1:0:0] https://u", "\n", ""])
def test_many_lines_are_read_in_linear_time(line: str) -> None:
    started = time.process_time()

    shown = lines_without_credentials([line] * 300_000)

    assert len(shown) == 300_000
    assert time.process_time() - started < 5  # quadratic would take minutes


DATED = "[2026-11-24 10:00:00,123] [INFO] by a@b"


@pytest.mark.parametrize(
    ("lines", "shown"),
    [
        (["x p", "q@h/x ok", "z", DATED], [URL_NOT_SHOWN, URL_NOT_SHOWN, "z", DATED]),
        (["a@b", "c", "d%40e", "z"], [*[URL_NOT_SHOWN] * 3, "z"]),
        (["x\ny", "q\uff20h"], [f"{URL_NOT_SHOWN}\n{URL_NOT_SHOWN}", URL_NOT_SHOWN]),
        (["q@h", "see https://h/x", "z"], [URL_NOT_SHOWN, "see https://h/x", "z"]),
        (["x", "https://h/x", DATED], ["x", "https://h/x", DATED]),
        ([DATED, "a@b"], [DATED, "a@b"]),
        ([], []),
    ],
)
def test_the_rest_of_a_record_that_starts_before_the_window_is_not_shown(
    lines: list[str], shown: list[str]
) -> None:
    assert lines_without_credentials(lines) == shown
