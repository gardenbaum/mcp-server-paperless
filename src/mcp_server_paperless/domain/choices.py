"""What the user is asked when a name matches several objects, and what they picked (decision-109).

A service raises ``Ambiguous`` with a ``Question``. The tool boundary may show it in a dialog and
run the call again with the user's pick, which answers exactly that question: ``picked`` returns
it. A pick confirms nothing.
"""

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence


@dataclass(frozen=True, slots=True)
class Choice:
    """One match.

    Attributes:
        value: What identifies it.
        title: What the user sees; ``owner`` is named after it.
        owner: The id of the user who owns it, if any.
    """

    value: str
    title: str
    owner: int | None = None


@dataclass(frozen=True, slots=True)
class Pick:
    """What the user picked.

    Attributes:
        topic: The ``topic`` of the question.
        question: Its ``fingerprint``.
        value: The value of the choice.
    """

    topic: str
    question: str
    value: str


def _hashed(about: object) -> str:
    return hashlib.sha256(repr(about).encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True, slots=True)
class Question:
    """Which of the matches a word means.

    Attributes:
        what: What the matches are, in the plural ("tags").
        word: What was asked for.
        choices: The matches, all of them.
    """

    what: str
    word: str
    choices: tuple[Choice, ...]

    @property
    def topic(self) -> str:
        """Identifies what was asked, whatever matches it."""
        return _hashed((self.what, self.word))

    @property
    def fingerprint(self) -> str:
        """Identifies the question: with another word, kind or match it is another one."""
        choices = [(c.value, c.title, c.owner) for c in self.choices]
        return _hashed((self.what, self.word, choices))

    def picking(self, value: str) -> Pick:
        """``value`` as the answer to this question."""
        return Pick(self.topic, self.fingerprint, value)


@dataclass(slots=True)
class Run:
    """One run of a call.

    Attributes:
        picks: What the user picked so far.
        written: Whether this run sent a write to Paperless.
    """

    picks: Sequence[Pick]
    written: bool = field(default=False, init=False)


_run: ContextVar[Run | None] = ContextVar("run_of_a_call", default=None)


@contextmanager
def picking(picks: Sequence[Pick]) -> Generator[Run]:
    """Run the block with these picks; the result says whether it wrote."""
    run = Run(picks)
    token = _run.set(run)
    try:
        yield run
    finally:
        _run.reset(token)


def _picks() -> Sequence[Pick]:
    run = _run.get()
    return run.picks if run else ()


def picked(question: Question) -> str | None:
    """The value the user picked for exactly this question, if they were asked it."""
    asked = question.fingerprint
    return next((pick.value for pick in _picks() if pick.question == asked), None)


def contradicts(question: Question) -> str | None:
    """The value the user last picked on the topic, if the only choice left is another one.

    A name that was ambiguous when the user was asked can be unique by now, but not mean what
    they picked.
    """
    last = next((p.value for p in reversed(_picks()) if p.topic == question.topic), None)
    return None if last in {None, *(choice.value for choice in question.choices)} else last


def note_write() -> None:
    """Note that the running call sends a write to Paperless, if a call is running."""
    run = _run.get()
    if run:
        run.written = True
