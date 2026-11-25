"""URLs without user name and password, in results and texts (decision-64, decision-66).

What a mail server answered to a login is in no text either (decision-80).
"""

import re
from itertools import groupby, islice, takewhile
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

# What stands for a URL whose credentials are not certain; its spaces keep it from being written.
URL_NOT_SHOWN: Final = "a URL that is not shown"
# A URL with credentials in the plain characters RFC 3986 gives them, which nobody reads otherwise.
_CREDENTIALS: Final = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[A-Za-z0-9._~!$&'()*+,;=:%-]*@([^@]*)"
)
# A URL that asks for the stored credentials to be removed (decision-64).
_EMPTY_USER: Final = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)(?::?@)+([^@/?#]*(?:[/?#].*)?)", flags=re.DOTALL
)
# An @, the characters some clients read as one, and its encoded form.
_AT: Final = re.compile("[@\\uff20\\ufe6b]|%40")
# Where the URL in a word may start: scheme and colon of a web address, or a colon and slashes.
_START: Final = re.compile(r"https?:|:[/\\]{2}", flags=re.IGNORECASE)
# A URL that nobody reads otherwise, and its authority.
_AUTHORITY: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[/\\]*([^/?#]*)")
# What tells a name, a path or an e-mail address from credentials without a scheme: they stand
# after a colon or two slashes, or before a port or an IPv6 address.
_BEFORE: Final = re.compile(r":|[/\\]{2}")
_PORT: Final = re.compile(r":[0-9]|@\[")
# After a colon or two slashes, more than what opens a value: it can be a part of a password.
_SECRET_BEFORE: Final = re.compile(r"(?::|[/\\]{2})[\"'`(\[{<=]*[^\"'`(\[{<=]")
_MAILTO: Final = re.compile(r"\bmailto:", flags=re.IGNORECASE)
# Where a record of a log starts; its other lines have no time.
_RECORD: Final = re.compile(r"\[[0-9]{4}-[0-9]{2}-[0-9]{2}[ T]")
# What stands for the answer of a mail server to a login, which can repeat the password.
ANSWER_NOT_SHOWN: Final = "(answer of the mail server not shown)"
# What stands for a line of a log record whose start was not read.
REST_NOT_SHOWN: Final = "(rest of a log record not shown)"
# In the traceback of a login that failed: its frame, and an error that quotes the server.
_LOGIN: Final = re.compile(r", in mailbox_login$", flags=re.MULTILINE)
_MAIL_ERROR: Final = re.compile(
    r"^ *(?:imaplib|imap_tools)\.[\w.]+:(?: |(?=\s)|\Z)", flags=re.MULTILINE
)
# What Paperless logs of a login that failed, or of the end of the connection after it, up to the
# name of the account, and the colon before the answer. A line of a log that is read has lost what
# is blank at its end.
_LOGGED: Final = re.compile(
    r"^(?:\[[^\n]*\] )?Error while (?:authenticating account|retrieving mailbox) ",
    flags=re.MULTILINE,
)
_ANSWER: Final = re.compile(r":(?: |(?=\s)|\Z)")


def without_credentials(url: str) -> str | None:
    """The URL without its credentials; None if it is not certain what else they are."""
    shown: str = url
    if "@" in url:
        found = _CREDENTIALS.fullmatch(url)
        if found is None:
            return None
        shown = found[1] + found[2]
    authority = _AUTHORITY.match(shown)
    unclear = _AT.search(authority[1] if authority else shown)
    unclear = unclear or re.search("[\\uff20\\ufe6b]", shown)
    return None if unclear else shown


def shown_url(url: str) -> str:
    """The URL as results hold it: without its credentials, or ``URL_NOT_SHOWN``."""
    shown = without_credentials(url)
    return URL_NOT_SHOWN if shown is None else shown


def without_empty_user(url: str) -> str:
    """The URL to write: one with an empty user name becomes one without any."""
    found = _EMPTY_USER.fullmatch(url)
    return found[1] + found[2] if found else url


def _credentials_in(word: str) -> bool:
    """Whether a word without a scheme is taken for a URL with credentials (decision-66)."""
    word = _MAILTO.sub("", word)
    at = _AT.search(word)
    if at is None:
        return False
    before = _BEFORE.search(word)
    return bool((before and _AT.search(word, before.end())) or _PORT.search(word, at.start()))


def _url_in(word: str) -> int | None:
    """Where the URL in a word of a text starts; None if there is none."""
    found = _START.search(word)
    if found is None:
        return 0 if _credentials_in(word) else None
    start = found.start()
    while found[0][0] == ":" and start and word[start - 1].isascii() and word[start - 1].isalpha():
        start -= 1
    # Only http and https are sure to be a scheme: other letters can be the end of a password.
    if _SECRET_BEFORE.search(word, 0, found.start()) and _AT.search(word, start):
        return 0  # the URL can be a part of credentials without a scheme
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


def _without_answers(text: str) -> str:
    """``text`` without what a mail server answered to a login.

    All goes from the answer on, line by line: nothing tells for sure where an answer ends.
    """
    logged = _LOGGED.search(text)
    answer = _ANSWER.search(text, logged.end()) if logged else None
    starts = [answer.end()] if answer else []
    if _LOGIN.search(text) and (error := _MAIL_ERROR.search(text)):
        starts.append(error.end())
    if not starts:
        return text
    start = min(starts)
    end = max(start, len(text.rstrip("\n")))
    lines = text.count("\n", start, end) + 1
    return text[:start] + "\n".join([ANSWER_NOT_SHOWN] * lines) + text[end:]


def text_without_credentials(text: str) -> str:
    """``text`` without the credentials of URLs and without the answer to a mail login.

    ``text`` is a record of a log or an error. The answers go first: they can hold URLs.
    """
    return _text_without_urls(_without_answers(text))


def _text_without_urls(text: str) -> str:
    """``text`` without the credentials of the URLs in it.

    Where they are certain, nothing else goes. Otherwise ``URL_NOT_SHOWN`` stands for all from
    the first URL to the last word with an @, once per line, because credentials may hold spaces,
    quotes, line breaks and URLs.
    """
    words = re.split(r"(\s+)", text)
    first = next((n for n, word in enumerate(words) if _url_in(word) is not None), len(words))
    end = max((n for n, word in enumerate(words) if _AT.search(word)), default=-1) + 1
    if end <= first:
        return text
    shown = [_shown_word(word) for word in words[first:end]]
    known = [word for word in shown if word is not None]
    lines = "".join(words[first:end]).split("\n")
    part = known if len(known) == len(shown) else ["\n".join(URL_NOT_SHOWN for _ in lines)]
    return "".join([*words[:first], *part, *words[end:]])


def lines_without_credentials(lines: Sequence[str]) -> list[str]:
    """The lines of a log without credentials of URLs, also of those split over lines.

    A URL is looked for in all lines of a record, but no further: what stands between it and an
    e-mail address of another record is not lost. The lines before the first record can be the rest
    of one whose URL or login is not among them: none of them is shown.
    """
    record = 0
    head = takewhile(lambda line: _RECORD.match(line) is None, lines)
    shown = ["\n".join(REST_NOT_SHOWN for _ in line.split("\n")) for line in head]

    def number(line: str) -> int:
        nonlocal record
        record += _RECORD.match(line) is not None
        return record

    for _, group in groupby(lines[len(shown) :], number):
        block = list(group)
        parts: Iterator[str] = iter(text_without_credentials("\n".join(block)).split("\n"))
        shown += ["\n".join(islice(parts, line.count("\n") + 1)) for line in block]
    return shown
