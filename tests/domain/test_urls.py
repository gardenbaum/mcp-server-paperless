"""URLs without their credentials, in values and in text."""

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


NOTE = "(credentials not shown) "


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("no url, a@b.example", "no url, a@b.example"),
        ("see https://h/x, a@b.example", "see https://h/x, a@b.example"),
        ("to 'https://u:p@h/x' ok", f"to {NOTE}'https://h/x' ok"),
        ("to https://u:p@q/r@h/x", f"to {NOTE}https://h/x"),
        ("a https://u:p://q@h b  http://v:w@i", f"a {NOTE}https://h b  {NOTE}http://i"),
    ],
)
def test_credentials_in_text(text: str, shown: str) -> None:
    assert text_without_credentials(text) == shown


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (
            "Webhook to https://u:p w@h/x: 401 for 'https://u:p@h/x'",
            "Webhook to https://h/x: 401 for ",
        ),
        ("Webhook to https://u:p w@h/x", "Webhook to https://h/x"),
        ("Webhook https://h/x", "Webhook https://h/x"),
    ],
)
def test_credentials_with_spaces_in_the_text_of_a_webhook(text: str, shown: str) -> None:
    found = text_without_credentials(text, spaces=True)

    assert found.startswith(NOTE + shown if "@" in text else shown)
    assert "u:p" not in found
