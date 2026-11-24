"""URLs without user name and password, in results and texts (decision-64, decision-66)."""

import re
from typing import Final

from mcp_server_paperless.domain.errors import without_credentials

# What stands for a URL whose credentials are not certain; its spaces keep it from being written.
URL_NOT_SHOWN: Final = "a URL that is not shown"
# A URL that asks for the stored credentials to be removed (decision-64).
_EMPTY_USER: Final = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://):?@([^@/?#]*(?:[/?#].*)?)", flags=re.DOTALL
)
# An @, the characters some clients read as one, and its encoded form.
_AT: Final = re.compile("[@\\uff20\\ufe6b]|%40")
# Where the URL in a word may start: scheme and colon of a web address, or a colon and slashes.
_START: Final = re.compile(r"https?:|:[/\\]{2}", flags=re.IGNORECASE)
# The authority of a URL.
_AUTHORITY: Final = re.compile(r"[^:]*:[/\\]*([^/?#]*)")


def shown_url(url: str) -> str:
    """The URL as results hold it: without its credentials, or ``URL_NOT_SHOWN``."""
    shown = without_credentials(url)
    if shown is None:
        return URL_NOT_SHOWN
    authority = _AUTHORITY.match(shown)
    unclear = (authority and _AT.search(authority[1])) or re.search("[\\uff20\\ufe6b]", shown)
    return URL_NOT_SHOWN if unclear else shown


def without_empty_user(url: str) -> str:
    """The URL to write: one with an empty user name becomes one without any."""
    found = _EMPTY_USER.fullmatch(url)
    return found[1] + found[2] if found else url


def _url_in(word: str) -> int | None:
    """Where the URL in a word of a text starts; None if there is none."""
    found = _START.search(word)
    if found is None:
        colon = word.find(":")
        return 0 if colon >= 0 and _AT.search(word, colon) else None
    start = found.start()
    while found[0][0] == ":" and start and word[start - 1].isascii() and word[start - 1].isalpha():
        start -= 1
    return start


def _shown_word(word: str) -> str | None:
    """A word with the URL in it as shown; None if it is not certain what its credentials are."""
    start = _url_in(word)
    if start is None:
        return None if _AT.search(word) else word
    url = word[start:]
    shown = shown_url(url)
    # Without an @ its credentials may go on in the next word.
    certain = _AT.search(url) and not _AT.search(word[:start]) and shown != URL_NOT_SHOWN
    return word[:start] + shown if certain else None


def text_without_credentials(text: str) -> str:
    """``text`` (a log line, an error) without the credentials of the URLs in it.

    Where they are certain, nothing else goes. Otherwise one ``URL_NOT_SHOWN`` stands for all from
    the first URL to the last word with an @, because credentials may hold spaces, quotes and URLs.
    """
    words = re.split(r"(\s+)", text)
    first = next((n for n, word in enumerate(words) if _url_in(word) is not None), len(words))
    end = max((n for n, word in enumerate(words) if _AT.search(word)), default=-1) + 1
    if end <= first:
        return text
    shown = [_shown_word(word) for word in words[first:end]]
    known = [word for word in shown if word is not None]
    part = known if len(known) == len(shown) else [URL_NOT_SHOWN]
    return "".join([*words[:first], *part, *words[end:]])
