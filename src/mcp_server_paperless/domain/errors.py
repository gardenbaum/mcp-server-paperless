"""Errors the server reports to clients.

Every message is client-safe: it never carries credentials, stack traces or raw upstream bodies.
``hint`` optionally names the next useful step.
"""

import hashlib
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence


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
    def for_effect(cls, effect: str, *, scope: object = None) -> ConfirmationRequired:
        """The standard refusal: nothing was changed, and confirming would ``effect``.

        ``effect`` is a verb phrase such as ``move document 5 to the trash``; it names what is
        touched but never document content. ``scope`` is what the text does not tell apart, such
        as the ids behind "and 5 more": anything whose ``repr`` is the same for the same targets.
        """
        return cls(f"Nothing was changed. Confirming would {_line(effect)}.", scope=scope)

    @classmethod
    def require(cls, effect: str, *, scope: object) -> None:
        """Refuse like ``for_effect``, unless the user accepted exactly this refusal.

        ``scope`` has no default: a caller whose text tells everything apart says so with None.

        Raises:
            ConfirmationRequired: The user has not accepted this effect on this ``scope``.
        """
        refusal = cls.for_effect(effect, scope=scope)
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


def quoted(text: str, limit: int = 60) -> str:
    """``text`` on one line, cut to ``limit`` characters with an ellipsis, in quotes.

    For naming a document or a note in a message without sending its whole content, and without
    characters that hide or reorder what the reader sees. Single
    quotes, or double quotes when the text holds a single quote; with both, the single ones
    become typographic apostrophes.
    """
    line = _line(text)
    shown = line if len(line) <= limit else line[: limit - 1] + "…"
    return _in_quotes(shown)


def _in_quotes(shown: str) -> str:
    if "'" not in shown:
        return f"'{shown}'"
    if '"' not in shown:
        return f'"{shown}"'
    return f"'{shown.replace(chr(39), chr(0x2019))}'"


# What a destination that is no address (a folder, a name) may hold besides: plain spaces, and the
# joiners some scripts need.
_IN_TEXT: Final = frozenset({" ", chr(0x200C), chr(0x200D)})
# The longest host name has 253 characters, the longest e-mail address 254.
DESTINATION_LIMIT: Final = 254


def both_ends(text: str, limit: int = 60) -> str:
    """``text`` like ``quoted``, but cut in its middle: what decides about a host is its end."""
    line = _line(text)
    if len(line) > limit:
        line = f"{line[: limit // 3]}…{line[-(limit - limit // 3 - 1) :]}"
    return quoted(line, limit)


# Categories no destination holds: control, format, unassigned, private and surrogate characters,
# and separators.
_NOT_SHOWN: Final = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp", "Zs"})
# Code points that may show as nothing although their category says otherwise (Unicode's
# Default_Ignorable_Code_Point, and the blank braille cell).
_IGNORABLE: Final = frozenset(
    {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0}
    | {*range(0x180B, 0x1810), *range(0xFE00, 0xFE10), *range(0xE0000, 0xE1000)}
)
type DestinationKind = Literal["host", "email", "url", "text"]
# What a character of a destination could be taken for: a comment, a quotation, another part.
_ODD_IN_EMAIL: Final = frozenset('()<>[]",;:\\')
_UNCERTAIN: Final = "it is not certain which host it leads to"
_ODD_IN_HOST: Final = frozenset("/\\#?@%")
_URL: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
# A URL with credentials in the plain characters RFC 3986 gives them, which nobody reads otherwise.
_CREDENTIALS: Final = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[A-Za-z0-9._~!$&'()*+,;=:%-]*@([^@]*)"
)


def without_credentials(url: str) -> str | None:
    """A service URL without its credentials; None if it is not certain what else they are."""
    if "@" not in url:
        return url
    found = _CREDENTIALS.fullmatch(url)
    return found[1] + found[2] if found else None


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


class DeliveryUnknown(PaperlessMcpError):
    """A request that sends something out failed in a way that does not say whether it was sent."""


class FeatureUnavailable(PaperlessMcpError):
    """The instance has the feature switched off (e.g. the audit log)."""


class UnsupportedVersion(PaperlessMcpError):
    """The Paperless instance does not speak the API version this server needs."""


class InvalidConfiguration(PaperlessMcpError):
    """The server's configuration is wrong or leaves it nothing to offer."""
