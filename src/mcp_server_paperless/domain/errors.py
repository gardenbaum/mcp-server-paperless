"""Errors the server reports to clients.

Every message is client-safe: it never carries credentials, stack traces or raw upstream bodies.
``hint`` optionally names the next useful step.
"""

import hashlib
import json
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit

from mcp_server_paperless.domain.urls import without_credentials

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Sequence


class PaperlessMcpError(Exception):
    """Base of every error with a client-safe message.

    Attributes:
        message: What went wrong, safe to show to the client.
        hint: The next useful step, if there is one.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        """Create the error from a client-safe message and an optional hint."""
        super().__init__(message)
        self.message: str = message
        self.hint: str | None = hint

    def describe(self) -> str:
        """The message, followed by the hint if there is one: what clients and users see."""
        return f"{self.message} Hint: {self.hint}" if self.hint else self.message


class NotFound(PaperlessMcpError):
    """The object does not exist or is not visible to the current user."""


class Ambiguous(PaperlessMcpError):
    """A name matches more than one object; the message lists the candidates."""


class Validation(PaperlessMcpError):
    """Paperless rejected the input."""


# What every refusal for a missing confirmation tells the caller to do.
CONFIRM_HINT = (
    "Describe the effect to the user and call the tool again with the same arguments and "
    "confirm=true only once they agree, or if they already asked for exactly this."
)


# Fillers and selectors that show as nothing though Unicode calls them letters or marks; not
# U+FE0F, which emoji use.
_FILLERS: Final = frozenset(
    {0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0, *range(0xFE00, 0xFE0F), *range(0xE0100, 0xE01F0)}
)
# Format characters that stay: the zero-width non-joiner and joiner, which some scripts and emoji
# need, and the marks that are visible signs before a number or word.
_FORMATS_KEPT: Final = frozenset(
    {0x200C, 0x200D, *range(0x0600, 0x0606), 0x06DD, 0x070F}
    | {0x0890, 0x0891, 0x08E2, 0x110BD, 0x110CD}
)
# Looks like a space but is none to Python.
_BLANK_BRAILLE: Final = chr(0x2800)


def _hidden(character: str) -> bool:
    """Whether ``character`` hides or reorders text: controls, format characters and fillers."""
    code = ord(character)
    if unicodedata.category(character) in {"Cc", "Cf"}:
        return code not in _FORMATS_KEPT and not character.isspace()
    return code in _FILLERS


def _line(text: str) -> str:
    """``text`` on one line, without characters that hide or reorder it."""
    return " ".join("".join(c for c in text if not _hidden(c)).replace(_BLANK_BRAILLE, " ").split())


# The fingerprint of the refusal the user has accepted, while the accepted call runs.
_accepted: ContextVar[str | None] = ContextVar("accepted_confirmation", default=None)


@contextmanager
def accepting(fingerprint: str | None) -> Generator[None]:
    """Let the confirmation with this fingerprint pass in the block; None accepts nothing."""
    token = _accepted.set(fingerprint)
    try:
        yield
    finally:
        _accepted.reset(token)


class ConfirmationRequired(Validation):
    """A destructive action needs a confirmation it does not have; nothing was changed.

    The one mechanism of the confirmation flow (decision-6, decision-15, decision-44): services
    call ``require`` after reading what the action would touch, so the message can say what would
    happen. The tool boundary shows the message to the user in a dialog where the client can, and
    runs the call again with the user's acceptance; with any other client it is an error, not a
    result, so it cannot be mistaken for a success, and ``confirm=true`` is the way on.

    Attributes:
        fingerprint: Identifies the message and what it is about, without holding either.
    """

    @classmethod
    def for_effect(
        cls, effect: str, *, scope: object = None, hint: str = CONFIRM_HINT
    ) -> ConfirmationRequired:
        """The standard refusal: nothing was changed, and confirming would ``effect``.

        ``effect`` is a verb phrase such as ``move document 5 to the trash``; it names what is
        touched but never document content, save the ``excerpt`` of a text that is corrected
        (decision-48). ``scope`` is what the text does not tell apart, such
        as the ids behind "and 5 more": anything whose ``repr`` is the same for the same targets.
        """
        return cls(
            f"Nothing was changed. Confirming would {_line(effect)}.", scope=scope, hint=hint
        )

    @classmethod
    def require(cls, effect: str, *, scope: object, hint: str = CONFIRM_HINT) -> None:
        """Refuse like ``for_effect``, unless the user accepted exactly this refusal.

        ``scope`` has no default: a caller whose text tells everything apart says so with None.
        ``hint`` is for a call that has to be repeated with other arguments.

        Raises:
            ConfirmationRequired: The user has not accepted this effect on this ``scope``.
        """
        refusal = cls.for_effect(effect, scope=scope, hint=hint)
        if _accepted.get() != refusal.fingerprint:
            raise refusal

    def __init__(self, message: str, *, hint: str = CONFIRM_HINT, scope: object = None) -> None:
        """Create the refusal from a client-safe message; ``hint`` defaults to ``CONFIRM_HINT``."""
        super().__init__(message, hint=hint)
        about = repr((message, scope)).encode("utf-8", "surrogatepass")
        self.fingerprint: str = hashlib.sha256(about).hexdigest()


class NotConfirmed(PaperlessMcpError):
    """The user was asked and did not accept; nothing was changed."""

    def __init__(self, reason: str) -> None:
        """Create the error; ``reason`` is a clause that names nothing of the target."""
        super().__init__(
            f"Nothing was changed: {reason}.",
            hint="Do not call the tool again on your own; ask the user what they want.",
        )
        self.reason: str = reason


def quoted(text: str, limit: int = 60) -> str:
    """``text`` on one line, cut to ``limit`` characters with an ellipsis, in quotes.

    For naming a document or a note in a message without sending its whole content, and without
    characters that hide or reorder what the reader sees. Single quotes, or double quotes when the
    text holds a single quote; with both, the single ones become typographic apostrophes. What
    else can be read as a double quote is escaped after the cut (decision-64): an escape does not
    count and is never cut itself, but a text that gets too long by them is cut earlier.
    """
    return _in_quotes(_cut(text, limit, lambda line, most: f"{line[: most - 1]}…"))


def _cut(text: str, limit: int, shorten: Callable[[str, int], str]) -> str:
    """``text`` on one line, escaped, with at most ``limit`` characters before the escapes.

    With them and its quotes it takes at most 240 characters, more only where ``limit`` is for a
    destination (600 for the longest).
    """
    line = _line(text)
    room = max(240, limit * 600 // DESTINATION_LIMIT) - 2
    while True:
        shown = _without_look_alikes(line if len(line) <= limit else shorten(line, limit))
        if len(shown) <= room:
            return shown
        limit -= 1


def _in_quotes(shown: str) -> str:
    if "'" not in shown:
        return f"'{shown}'"
    if '"' not in shown:
        return f'"{shown}"'
    return f"'{shown.replace(chr(39), chr(0x2019))}'"


# What a destination that is no address (a folder, a name) may hold besides: plain spaces, and the
# joiners some scripts need.
_JOINERS: Final = frozenset({chr(0x200C), chr(0x200D)})
_IN_TEXT: Final = _JOINERS | {" "}
# The longest host name has 253 characters, the longest e-mail address 254.
DESTINATION_LIMIT: Final = 254


def both_ends(text: str, limit: int = 60) -> str:
    """``text`` like ``quoted``, but cut in its middle: what decides about a host is its end."""
    return _in_quotes(
        _cut(text, limit, lambda line, m: f"{line[: m // 3]}…{line[-(m - m // 3 - 1) :]}")
    )


# Categories no destination holds: control, format, unassigned, private and surrogate characters,
# and separators.
_NOT_SHOWN: Final = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp", "Zs"})
# Code points that may show as nothing although their category says otherwise (Unicode's
# Default_Ignorable_Code_Point, and the blank braille cell).
_IGNORABLE: Final = frozenset(
    {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0}
    | {*range(0x180B, 0x1810), *range(0xFE00, 0xFE10), *range(0xE0000, 0xE1000)}
)

# What can be read as a double quote, and so as the end of an excerpt, beside the double quote and
# the backslash. Guillemets stay: nothing this server writes looks like one.
_DOUBLE_QUOTES: Final = frozenset(
    {0x22, 0x5C, 0x02BA, 0x02DD, 0x02EE, 0x02F5, 0x02F6, 0x05F2, 0x05F4, 0x2033, 0x2034, 0x2036}
    | {0x2037, 0x2057, 0x275D, 0x275E, 0x2760, 0x2E42, 0x3003, 0xFF02, 0x030E, 0x1CD3, 0x1425}
    | {*range(0x201C, 0x2020), *range(0x301D, 0x3020), *range(0x1F676, 0x1F679)}
)
# Two of these side by side can be, too.
_SINGLE_QUOTES: Final = frozenset(
    {0x27, 0x60, 0xB4, 0x02B9, 0x02BB, 0x02BC, 0x02BD, 0x02C8, 0x02CA, 0x02CB, 0x0384, 0x055A}
    | {0x05F3, 0x1FBF, 0x1FFD, 0x1FFE, 0x2032, 0x2035, 0x275B, 0x275C, 0xFF07, 0xFF40}
    | {0x02BE, 0x02BF, 0x02F4, 0x0374, 0x07F4, 0x07F5, 0x144A, 0x16CC, 0x1FBD, 0x1FEF}
    | {0xA78B, 0xA78C}
    | {*range(0x2018, 0x201C)}
)
# Signs and letters that show as nothing: a musical null notehead and hieroglyphic blanks.
_BLANK: Final = frozenset({0x1D159, 0x13441, 0x13442})
_NOT_IN_EXCERPT: Final = _IGNORABLE | _BLANK | _DOUBLE_QUOTES


def _escape(character: str) -> str:
    escape = json.dumps(character)[1:-1]
    return f"\\u{ord(character):04x}" if escape == character else escape


def _escaped(text: str) -> Iterator[str]:
    """The characters of ``text`` as ``excerpt`` shows them."""
    before, carries = "", False  # the character before, and whether a mark can stand on it
    for character in text:
        code, category = ord(character), unicodedata.category(character)
        if character == " ":  # a refusal folds doubled spaces into one
            hides = before == " "
        elif code in _SINGLE_QUOTES:
            hides = before != "" and ord(before) in _SINGLE_QUOTES
        else:
            hides = (
                category in _NOT_SHOWN
                or code in _NOT_IN_EXCERPT
                or (category[0] == "M" and not carries)
            )
        yield _escape(character) if hides else character
        before, carries = character, not hides and category[0] in "LMN"


# Hebrew writes with two of them: the Yiddish double yod is a letter that two single ones replace
# anyway, and gershayim stand between the letters of an acronym.
_DOUBLE_YOD: Final = 0x05F2
_GERSHAYIM: Final = 0x05F4
_HEBREW: Final = range(0x05D0, 0x05F3)
# Double accents, which look like a double quote where no letter carries them.
_DOUBLE_ACCENTS: Final = frozenset({0x030B, 0x030F})


def _invisible(character: str) -> bool:
    """Whether ``character``, one that ``_line`` keeps, shows as nothing."""
    code, category = ord(character), unicodedata.category(character)
    if category == "Cf":
        return character in _JOINERS
    return code in _IGNORABLE or code in _BLANK or (category in _NOT_SHOWN and character != " ")


def _without_look_alikes(text: str) -> str:
    """``text`` with what can be read as a double quote escaped.

    The double quote itself, which decides about the quotes around, the backslash, which is none,
    and what Hebrew words hold stay.
    """
    shown = ""
    base = " "  # the last character that is neither invisible nor a mark
    carries = False  # whether a letter is right before, perhaps with marks on it
    for character, after in zip(text, f"{text[1:]} ", strict=False):
        code, category = ord(character), unicodedata.category(character)
        invisible = _invisible(character)
        if code in _SINGLE_QUOTES:
            hides = ord(base) in _SINGLE_QUOTES
        elif code in _DOUBLE_ACCENTS:
            hides = not carries
        elif code == _GERSHAYIM:
            hides = not (ord(base) in _HEBREW and ord(after) in _HEBREW)
        else:
            hides = code in _DOUBLE_QUOTES and code not in {0x22, 0x5C, _DOUBLE_YOD}
        shown += _escape(character) if hides else character
        if invisible or hides:
            carries = False
        elif category[0] != "M":
            carries = category[0] in "LN"
        if not invisible and category[0] != "M":
            base = character
    return shown


def excerpt(text: str) -> str:
    """``text`` in full in double quotes, as JSON, with everything that may not show escaped.

    So is everything that can be read as the closing quote. For showing a piece of document text
    whose whitespace and hidden characters matter (decision-48); ``quoted`` folds and drops those.
    """
    return f'"{"".join(_escaped(text))}"'


type DestinationKind = Literal["host", "email", "url", "text"]
# What a character of a destination could be taken for: a comment, a quotation, another part.
_ODD_IN_EMAIL: Final = frozenset('()<>[]",;:\\')
_UNCERTAIN: Final = "it is not certain which host it leads to"
_ODD_IN_HOST: Final = frozenset("/\\#?@%")
_URL: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


def _host_of(text: str, kind: DestinationKind) -> str:
    """The host a destination leads to.

    Raises:
        ValueError: ``text`` has a form in which another part could be taken for the host.
    """
    host = text
    if kind == "email":
        local, _, host = text.rpartition("@")
        if not local or "@" in local or not _ODD_IN_EMAIL.isdisjoint(text):
            raise ValueError
    elif kind == "url" and _URL.match(text):
        host = urlsplit(text).hostname or ""
        if "\\" in text or (host.isascii() and not text.isascii()):
            raise ValueError
    if not host or not _ODD_IN_HOST.isdisjoint(host):
        raise ValueError
    return host


def _resolved(host: str) -> str:
    """The form a host with letters beyond ASCII is looked up in (``xn--...``); "" for ASCII.

    Raises:
        ValueError: The 2003 rules (Python's ``idna`` codec, and so many clients) and plain
            Punycode (the 2008 rules, as browsers) give different hosts, or the codec none.
    """
    if host.isascii():
        return ""
    lowered = host.lower()
    # Letters whose case rules changed over time (sharp s, final sigma, Cherokee): what the codec
    # answers for them depends on the Python build.
    if lowered != host.casefold():
        raise ValueError
    # Not of the lowered text: the 2003 rules know fewer capital letters.
    found = host.encode("idna").decode().lower()
    plain = ".".join(
        label if label.isascii() else "xn--" + label.encode("punycode").decode()
        for label in lowered.split(".")
    )
    if found != plain:
        raise ValueError
    return found


def _shows(character: str, kind: DestinationKind) -> bool:
    """Whether a character of a destination is sure to show; text may hold a little more."""
    if kind == "text" and character in _IN_TEXT:
        return True
    return unicodedata.category(character) not in _NOT_SHOWN and ord(character) not in _IGNORABLE


def destination(text: str, what: str, kind: DestinationKind = "host") -> str:
    """``text``, where something would be sent, quoted and in full.

    A service URL is shown without its credentials. If its host has letters beyond ASCII, the form
    it is looked up in follows, so that letters that look like others are visible.

    Raises:
        Validation: The user could not be shown where it leads; the message says why.
            ``confirm=true`` does not get past this.
    """
    if kind == "url":
        public = without_credentials(text)
        if public is None:
            raise not_shown(what, _UNCERTAIN)
        text = public
    shown, resolved, cause = _in_quotes(text), "", ""
    if len(text) > DESTINATION_LIMIT:
        cause = f"it has more than {DESTINATION_LIMIT} characters"
    elif not all(_shows(character, kind) for character in text):
        cause = "it holds a character that may not show"
    elif shown[1:-1] != text:
        cause = "it holds both kinds of quotes"
    elif "  " in text:
        cause = "it holds doubled spaces"
    elif kind != "text":
        try:
            resolved = _resolved(_host_of(text, kind))
        except ValueError:
            cause = _UNCERTAIN
    if cause:
        raise not_shown(what, cause, text)
    return f"{shown} ({resolved})" if resolved else shown


def not_shown(what: str, cause: str, text: str | None = None) -> Validation:
    """The refusal of a destination, with both ends of ``text`` unless that may hold a secret."""
    named = f" {both_ends(text)}" if text is not None else ""
    message = (
        f"Nothing was changed: the {what}{named} cannot be shown to the user in full, because "
        f"{cause}."
    )
    return Validation(message, hint="Correct it, or make this change in the Paperless web UI.")


def listed(names: Sequence[str], limit: int = 5) -> str:
    """The first ``limit`` names quoted, then "and N more"; "none" for an empty list."""
    if not names:
        return "none"
    shown = ", ".join(quoted(name) for name in names[:limit])
    rest = len(names) - limit
    return f"{shown} and {rest} more" if rest > 0 else shown


class AuthenticationFailed(PaperlessMcpError):
    """Paperless rejected the credential (wrong, expired or revoked token)."""


class PermissionDenied(PaperlessMcpError):
    """The current user may not perform the action."""


class Conflict(PaperlessMcpError):
    """The action conflicts with the current state of the object."""


class UpstreamUnavailable(PaperlessMcpError):
    """Paperless is unreachable, too slow or temporarily failing.

    Attributes:
        status: The HTTP status Paperless (or a proxy before it) answered, if there was an answer.
    """

    def __init__(self, message: str, *, hint: str | None = None, status: int | None = None) -> None:
        """Create the error; ``status`` is the HTTP status of the answer, if any."""
        super().__init__(message, hint=hint)
        self.status: int | None = status


class UpstreamTimeout(UpstreamUnavailable):
    """Paperless did not answer within the timeout."""


class UpstreamUnreachable(UpstreamUnavailable):
    """No connection to Paperless could be made."""


class UnexpectedAnswer(PaperlessMcpError):
    """Paperless answered a request with success, but with something that cannot be read."""


class AnswerCompressed(PaperlessMcpError):
    """An answer was compressed although that was not asked for."""


class AnswerTooLarge(PaperlessMcpError):
    """An answer of Paperless is larger than what may be read of it."""


class DeliveryUnknown(PaperlessMcpError):
    """A request that sends something out failed in a way that does not say whether it was sent."""


class FeatureUnavailable(PaperlessMcpError):
    """The instance has the feature switched off (e.g. the audit log)."""


class UnsupportedVersion(PaperlessMcpError):
    """The Paperless instance does not speak the API version this server needs."""


class InvalidConfiguration(PaperlessMcpError):
    """The server's configuration is wrong or leaves it nothing to offer."""
