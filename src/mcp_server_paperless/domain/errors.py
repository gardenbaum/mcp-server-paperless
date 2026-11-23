"""Errors the server reports to clients.

Every message is client-safe: it never carries credentials, stack traces or raw upstream bodies.
``hint`` optionally names the next useful step.
"""

import hashlib
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

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
    if "'" not in shown:
        return f"'{shown}'"
    if '"' not in shown:
        return f'"{shown}"'
    return f"'{shown.replace(chr(39), chr(0x2019))}'"


# The longest host name has 253 characters, the longest e-mail address 254.
DESTINATION_LIMIT: Final = 254


def both_ends(text: str, limit: int = 60) -> str:
    """``text`` like ``quoted``, but cut in its middle: what decides about a host is its end."""
    line = _line(text)
    if len(line) > limit:
        line = f"{line[: limit // 3]}…{line[-(limit - limit // 3 - 1) :]}"
    return quoted(line, limit)


def destination(text: str, what: str) -> str:
    """``text``, where something would be sent, quoted and in full.

    Raises:
        Validation: It is too long or holds characters a message does not show, so the user could
            not see where it leads. ``confirm=true`` does not get past this.
    """
    shown = quoted(text, DESTINATION_LIMIT)
    if shown[1:-1] != text:
        message = (
            f"Nothing was changed: the {what} {both_ends(text)} cannot be shown to the user in "
            f"full (over {DESTINATION_LIMIT} characters, or with characters that do not show)."
        )
        raise Validation(message, hint="Correct it, or make this change in the Paperless web UI.")
    return shown


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
