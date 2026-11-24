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
NOTE: Final = "(credentials not shown) "


def shown_url(url: str) -> str:
    """The URL as results hold it: without its credentials, or ``URL_NOT_SHOWN``."""
    shown = without_credentials(url)
    return URL_NOT_SHOWN if shown is None else shown


def without_empty_user(url: str) -> str:
    """The URL to write: one with an empty user name becomes one without any."""
    found = _EMPTY_USER.fullmatch(url)
    return found[1] + found[2] if found else url


def _word(word: str) -> str:
    """A word of a text without what stands between its first :// and its last @."""
    head, scheme, rest = word.partition("://")
    return NOTE + head + scheme + rest.rpartition("@")[2] if "@" in rest else word


def text_without_credentials(text: str, *, spaces: bool = False) -> str:
    """``text`` (a log line, an error) without the credentials of the URLs in it.

    With ``spaces`` they may hold spaces: then everything up to the last @ after a URL's scheme
    goes, at the cost of other text in between.
    """
    shown = "".join(map(_word, re.split(r"(\s+)", text)))
    if not spaces:
        return shown
    first, *rest = shown.split("://")
    cut = [part.rpartition("@")[2] for part in rest]
    return shown if cut == rest else NOTE + "://".join([first, *cut])


def log_line(line: str) -> str:
    """A line of a Paperless log without credentials: Paperless logs the URLs it calls."""
    return text_without_credentials(line, spaces="webhook" in line.lower())
